##
## This file is part of the libsigrokdecode project.
##
## Copyright (C) 2020 Thomas Hoffmann <th.hoffmann@mailbox.org>
##
## based on DS1307:
##
## Copyright (C) 2012-2020 Uwe Hermann <uwe@hermann-uwe.de>
## Copyright (C) 2013 Matt Ranostay <mranostay@gmail.com>
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, see <http://www.gnu.org/licenses/>.
##

import re
import sigrokdecode as srd
from common.srdhelper import bcd2int, SrdIntEnum

days_of_week = {'Monday': ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'),
                'Sunday': ('Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'),
                'Saturday': ('Saturday', 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')}

#days_of_week = (
#    'Sunday', 'Monday', 'Tuesday', 'Wednesday',
#    'Thursday', 'Friday', 'Saturday',
#)

#DS3231: registers 00h to 12h
regs = (
    'Seconds', 'Minutes', 'Hours', 'Day', 'Date', 'Month', 'Year',
    'Alarm1 Seconds', 'Alarm1 Minutes', 'Alarm1 Hours', 'Alarm1 Day/Date', 
    'Alarm2 Minutes', 'Alarm2 Hours', 'Alarm2 Day/Date', 'Control',
    'Control/Status', 'Aging Offset', 'Temperature MSB', 'Temperature LSB',
)

bits = (
    'Seconds', 'Reserved', 'Minutes', '12/24 hours', 'AM/PM',
    'Hours', 'Day', 'Date', 'Century', 'Month', 'Year', 'Day/Date', '/EOSC', 'BBSQWE',
    'CONV', 'RATE', 'INTCN', 'A2IE', 'A1IE', 'RS1', 'OSF', 'EN32kHz', 
    'BSY', 'A2F', 'A1F', 'A1M1', 'A1M2', 'A1M3', 'A1M4', 'A2M2', 'A2M3', 'A2M4',
    'TMSB', 'TLSB', 'AOFS', 
)

blocks = (
    'Date Time', 'Alarm1', 'Alarm2', 'Temperature',
)

# used only for DS3231(SN), bits reserved for DS3231M (always 1Hz)
rates = {
    0b00: '1Hz',
    0b01: '1024Hz',
    0b10: '4096Hz',
    0b11: '8192Hz',
}

DS3231_I2C_ADDRESS = 0x68

def regs_and_bits_and_blocks():
    l = [('reg_' + re.sub('\\/| ', '_', r).lower(), r + ' register') for r in regs]
    l += [('bit_' + re.sub('\\/| ', '_', b).lower(), b + ' bit') for b in bits]
    l += [('block_' + re.sub('\\/| ', '_', bl).lower(), bl + ' block') for bl in blocks]
    return tuple(l)

a = ['REG_' + re.sub('\\/| ', '_',r).upper() for r in regs] + \
    ['BIT_' + re.sub('\\/| ', '_', b).upper() for b in bits] + \
    ['BLOCK_' + re.sub('\\/| ', '_', bl).upper() for bl in blocks] + \
    ['WARNING']
Ann = SrdIntEnum.from_list('Ann', a)    

class Decoder(srd.Decoder):
    api_version = 3
    id = 'ds3231'
    name = 'DS3231'
    longname = 'Dallas DS3231'
    desc = 'Dallas DS3231 realtime clock module protocol.'
    license = 'gplv2+'
    inputs = ['i2c']
    outputs = []
    tags = ['Clock/timing', 'IC']
    options = ( 
        {'id': 'subtype', 'desc': 'DS3231SN (TXCO) or DS3231M (MEMS)',
         'default': 'SN', 'values': ('SN', 'M')
        }, 
        {'id': 'regptr', 'desc': 'value of register pointer at begin of capture',
         'default': 0,
         'values': tuple(range(0x13))
        }, 
        {'id': 'fdw', 'desc': 'First day of week', 'default': 'Monday',
         'values': ('Monday', 'Sunday', 'Saturday')
        },
    )
    annotations =  regs_and_bits_and_blocks() + (
        ('warning', 'Warning'),
    )
    annotation_rows = (
        ('bits', 'Bits', Ann.prefixes('BIT_')),
        ('regs', 'Registers', Ann.prefixes('REG_')),
        ('block_data', 'Block Data', Ann.prefixes('BLOCK_')),
        ('warnings', 'Warnings', (Ann.WARNING,)),
    )

    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 'IDLE'
        self.bits = []
        #date time
        self.seconds = -1
        self.minutes = -1
        self.ampm = -1
        self.hours = -1
        self.days = -1
        self.date = -1
        self.months = -1
        #alarm 1
        self.a1m1 = -1
        self.al1seconds = -1
        self.a1m2 = -1
        self.al1minutes = -1
        self.a1m3 = -1
        self.a1ampm = -1
        self.al1hours = -1
        #alarm 2
        self.a2m2 = -1
        self.al2minutes = -1
        self.a2m3 = -1
        self.a2ampm = -1
        self.al2hours = -1
        #temperature
        self.tempMSB = -1
        #regs
        self.startreg = -1
        self.inblock = -1
        self.blockmode = ''        
        
    def start(self):
        self.out_ann = self.register(srd.OUTPUT_ANN)
        self.reg = self.options['regptr']

    def putd(self, bit1, bit2, data):
        self.put(self.bits[bit1][1], self.bits[bit2][2], self.out_ann, data)

    def putr(self, bit):
        self.put(self.bits[bit][1], self.bits[bit][2], self.out_ann,
                 [Ann.BIT_RESERVED, ['Reserved bit', 'Reserved', 'Rsvd', 'R']])

    def handle_reg_0x00(self, b, rw): # Seconds (0-59)
        self.putd(7, 0, [Ann.REG_SECONDS, ['Seconds', 'Sec', 'S']])
        s = self.seconds = bcd2int(b & 0x7f)
        self.putr(7)
        self.putd(6, 0, [Ann.BIT_SECONDS, ['Second: %d' % s, 'Sec: %d' % s, 'S: %d' % s, 'S']])
        #block
        self.startreg = self.ss
        self.inblock = 0
        self.blockmode = rw
        
    def handle_reg_0x01(self, b, rw): # Minutes (0-59)
        self.putd(7, 0, [Ann.REG_MINUTES, ['Minutes', 'Min', 'M']])
        self.putr(7)
        m = self.minutes = bcd2int(b & 0x7f)
        self.putd(6, 0, [Ann.BIT_MINUTES, ['Minute: %d' % m, 'Min: %d' % m, 'M: %d' % m, 'M']])
        self.inblock = 1 if self.inblock == 0 and self.blockmode == rw else -1

    def handle_reg_0x02(self, b, rw): # Hours (1-12+AM/PM or 0-23)
        self.putd(7, 0, [Ann.REG_HOURS, ['Hours', 'H']])
        self.putr(7)
        if (b & (1 << 6)):
            self.putd(6, 6, [Ann.BIT_12_24_HOURS, ['12-hour mode', '12h mode', '12h']])
            self.ampm = 'PM' if (b & (1 << 5)) else 'AM'
            self.putd(5, 5, [Ann.BIT_AM_PM, [self.ampm, self.ampm[0]]])
            self.hours = bcd2int(b & 0x1f)
            self.putd(4, 0, [Ann.BIT_HOURS, ['Hour: %d' % self.hours, 'H: %d' % self.hours, 'H']])
        else:
            self.putd(6, 6, [Ann.BIT_12_24_HOURS, ['24-hour mode', '24h mode', '24h']])
            self.ampm = ''
            self.hours = bcd2int(b & 0x3f)
            self.putd(5, 0, [Ann.BIT_HOURS, ['Hour: %d' % self.hours, 'H: %d' % self.hours, 'H']])
        self.inblock = 2 if self.inblock == 1 and self.blockmode == rw else -1    

    def handle_reg_0x03(self, b, rw): # Day / day of week (1-7)
        self.putd(7, 0, [Ann.REG_DAY, ['Day of week', 'Day', 'D']])
        for i in (7, 6, 5, 4, 3):
            self.putr(i)
        self.days = bcd2int(b & 0x07)
        ws = days_of_week[self.options['fdw']][self.days - 1]
        self.putd(2, 0, [Ann.BIT_DAY, ['Weekday: %s' % ws, 'WD: %s' % ws, 'WD', 'W']])
        self.inblock = 3 if self.inblock == 2 and self.blockmode == rw else -1

    def handle_reg_0x04(self, b, rw): # Date (1-31)
        self.putd(7, 0, [Ann.REG_DATE, ['Date', 'D']])
        for i in (7, 6):
            self.putr(i)
        d = self.date = bcd2int(b & 0x3f)
        self.putd(5, 0, [Ann.BIT_DATE, ['Date: %d' % d, 'D: %d' % d, 'D']])
        self.inblock = 4 if self.inblock == 3 and self.blockmode == rw else -1

    def handle_reg_0x05(self, b, rw): # Month (1-12)
        self.putd(7, 0, [Ann.REG_MONTH, ['Month', 'Mon', 'M']])
        century = 1 if (b & (1 << 7)) else 0
        self.putd(7, 7, [Ann.BIT_CENTURY, ['Century overflow: %d' % century,
        'Cent OVF: %d' % century, 'CO: %d' % century, 'CO']])
        for i in (6, 5):
            self.putr(i)
        m = self.months = bcd2int(b & 0x1f)
        self.putd(4, 0, [Ann.BIT_MONTH, ['Month: %d' % m, 'Mon: %d' % m, 'M: %d' % m, 'M']])
        self.inblock = 5 if self.inblock == 4 and self.blockmode == rw else -1

    def handle_reg_0x06(self, b, rw): # Year (0-99)
        self.putd(7, 0, [Ann.REG_YEAR, ['Year', 'Y']])
        y = bcd2int(b & 0xff)
        year = y + 2000
        self.putd(7, 0, [Ann.BIT_YEAR, ['Year: %d' % year, 'Y: %d' % y, 'Y']])
        #block
        if self.inblock == 5 and self.blockmode == rw :
            d = 'Date / time: %s, %02d.%02d.%4d %02d:%02d:%02d%s' % (
                days_of_week[self.options['fdw']][self.days - 1], self.date, self.months,
                year, self.hours, self.minutes, self.seconds, self.ampm)
            self.put(self.startreg, self.es, self.out_ann, 
                [Ann.BLOCK_DATE_TIME, ['%s %s' % (rw, d)]])
        self.inblock = -1

    def handle_reg_0x07(self, b, rw): # Alarm1 Seconds (0-59)
        self.putd(7, 0, [Ann.REG_ALARM1_SECONDS, ['Alarm1 Seconds', 'Al1 Sec', 'A1S']])
        self.a1m1 = 1 if (b & (1 << 7)) else 0
        self.putd(7, 7, [Ann.BIT_A1M1, ['A1M1: %d' % self.a1m1, 'A1M1']])
        s = self.al1seconds = bcd2int(b & 0x7f)
        self.putd(6, 0, [Ann.BIT_SECONDS, ['Second: %d' % s, 'Sec: %d' % s, 'S: %d' % s, 'S']])
        self.startreg = self.ss
        self.inblock = 7
        self.blockmode = rw 

    def handle_reg_0x08(self, b, rw): # Alarm1 Minutes (0-59)
        self.putd(7, 0, [Ann.REG_ALARM1_MINUTES, ['Alarm1 Minutes', 'Al1 Min', 'A1M']])
        self.a1m2 = 1 if (b & (1 << 7)) else 0
        self.putd(7, 7, [Ann.BIT_A1M2, ['A1M2: %d' % self.a1m2, 'A1M2']])
        m = self.al1minutes = bcd2int(b & 0x7f)
        self.putd(6, 0, [Ann.BIT_MINUTES, ['Minute: %d' % m, 'Min: %d' % m, 'M: %d' % m, 'M']])
        self.inblock = 8 if self.inblock == 7 and self.blockmode == rw else -1

    def handle_reg_0x09(self, b, rw): # Alarm1 Hours (1-12+AM/PM or 0-23)
        self.putd(7, 0, [Ann.REG_ALARM1_HOURS, ['Alarm1 Hours', 'Al1 Hr', 'A1H']])
        self.a1m3 = 1 if (b & (1 << 7)) else 0
        self.putd(7, 7, [Ann.BIT_A1M3, ['A1M3: %d' % self.a1m3, 'A1M3']])
        if (b & (1 << 6)):
            self.putd(6, 6, [Ann.BIT_12_24_HOURS, ['12-hour mode', '12h mode', '12h']])
            self.a1ampm = 'PM' if (b & (1 << 5)) else 'AM'
            self.putd(5, 5, [Ann.BIT_AM_PM, [self.a1ampm, self.a1ampm[0]]])
            self.al1hours = bcd2int(b & 0x1f)
            self.putd(4, 0, [Ann.BIT_HOURS, ['Hour: %d' % self.al1hours, 'H: %d' % self.al1hours, 'H']])
        else:
            self.putd(6, 6, [Ann.BIT_12_24_HOURS, ['24-hour mode', '24h mode', '24h']])
            self.a1ampm = ''
            self.al1hours = bcd2int(b & 0x3f)
            self.putd(5, 0, [Ann.BIT_HOURS, ['Hour: %d' % self.al1hours, 'H: %d' % self.al1hours, 'H']])
        self.inblock = 9 if self.inblock == 8 and self.blockmode == rw else -1

    def handle_reg_0x0a(self, b, rw): # Alarm1 Date or Day / day of week (1-7)
        self.putd(7, 0, [Ann.REG_ALARM1_DAY_DATE, ['Alarm1 Date or Day of week', 'Al1 Day / DOW', 'A1DD']])
        a1m4 = 1 if (b & (1 << 7)) else 0
        self.putd(7, 7, [Ann.BIT_A1M4, ['A1M4: %d' % a1m4, 'A1M4']])
        a1dydt = 1 if (b & (1 << 6)) else 0
        self.putd(6, 6, [Ann.BIT_DAY_DATE, ['DYDT: %d' % a1dydt, 'DYDT']])
        if a1dydt == 1:        
            w = bcd2int(b & 0x07)
            ws = days_of_week[self.options['fdw']][w - 1]
            self.putd(2, 0, [Ann.BIT_DAY, ['Weekday: %s' % ws, 'WD: %d' % w, 'WD', 'W']])
        else:
            da = bcd2int(b & 0x3f)
            self.putd(5, 0, [Ann.BIT_DATE, ['Date / Day: %d' % da, 'D: %d' % da, 'D']])
        #block
        if self.inblock == 9 and self.blockmode == rw :
            if (self.a1m1, self.a1m2, self.a1m3, a1m4) == (1, 1, 1, 1):
                d = 'every second'
            elif (self.a1m1, self.a1m2, self.a1m3, a1m4) == (0, 1, 1, 1):
                d = 'every minute, second=%02d' % self.al1seconds
            elif (self.a1m1, self.a1m2, self.a1m3, a1m4) == (0, 0, 1, 1):
                d = 'every hour, mm:ss=%02d:%02d' % (self.al1minutes, self.al1seconds)
            elif (self.a1m1, self.a1m2, self.a1m3, a1m4) == (0, 0, 0, 1):
                d = 'daily, hh:mm:ss=%02d:%02d:%02d%s' % (self.al1hours, self.al1minutes, self.al1seconds, self.a1ampm)
            elif (self.a1m1, self.a1m2, self.a1m3, a1m4) == (0, 0, 0, 0):
                if a1dydt == 1:
                    daydate = ws
                else:
                    daydate = '%d. of every month' % da
                d = '%s, %02d:%02d:%02d' % (
                    daydate, self.al1hours, self.al1minutes, self.al1seconds)
            else:
                d = 'invalid setting'  #FIXME: print warning
            d = 'Alarm1: ' + d    
            self.put(self.startreg, self.es, self.out_ann, 
                [Ann.BLOCK_ALARM1, ['%s %s' % (rw, d)]])
        self.inblock = -1

    def handle_reg_0x0b(self, b, rw): # Alarm2 Minutes (0-59)
        self.putd(7, 0, [Ann.REG_ALARM2_MINUTES, ['Alarm2 Minutes', 'Al2 Min', 'A2M']])
        self.a2m2 = 1 if (b & (1 << 7)) else 0
        self.putd(7, 7, [Ann.BIT_A2M2, ['A2M2: %d' % self.a2m2, 'A2M2']])
        m = self.al2minutes = bcd2int(b & 0x7f)
        self.putd(6, 0, [Ann.BIT_MINUTES, ['Minute: %d' % m, 'Min: %d' % m, 'M: %d' % m, 'M']])
        self.startreg = self.ss
        self.inblock = 0x0b
        self.blockmode = rw 

    def handle_reg_0x0c(self, b, rw): # Alarm2 Hours (1-12+AM/PM or 0-23)
        self.putd(7, 0, [Ann.REG_ALARM2_HOURS, ['Alarm2 Hours', 'Al2 Hr', 'A2H']])
        self.a2m3 = 1 if (b & (1 << 7)) else 0
        self.putd(7, 7, [Ann.BIT_A2M3, ['A2M3: %d' % self.a2m3, 'A2M3']])
        if (b & (1 << 6)):
            self.putd(6, 6, [Ann.BIT_12_24_HOURS, ['12-hour mode', '12h mode', '12h']])
            self.a2ampm = 'PM' if (b & (1 << 5)) else 'AM'
            self.putd(5, 5, [Ann.BIT_AM_PM, [self.a2ampm, self.a2ampm[0]]])
            h = self.al2hours = bcd2int(b & 0x1f)
            self.putd(4, 0, [Ann.BIT_HOURS, ['Hour: %d' % h, 'H: %d' % h, 'H']])
        else:
            self.putd(6, 6, [Ann.BIT_12_24_HOURS, ['24-hour mode', '24h mode', '24h']])
            self.a2ampm = ''
            h = self.al2hours = bcd2int(b & 0x3f)
            self.putd(5, 0, [Ann.BIT_HOURS, ['Hour: %d' % h, 'H: %d' % h, 'H']])
        self.inblock = 0x0c if self.inblock == 0x0b and self.blockmode == rw else -1

    def handle_reg_0x0d(self, b, rw): # Alarm2 Date or Day / day of week (1-7)
        self.putd(7, 0, [Ann.REG_ALARM2_DAY_DATE, ['Alarm2 Date or Day of week', 'Al2 Day / DOW', 'A2DD']])
        a2m4 = 1 if (b & (1 << 7)) else 0
        self.putd(7, 7, [Ann.BIT_A2M4, ['A2M4: %d' % a2m4, 'A2M4']])
        a2dydt = 1 if (b & (1 << 6)) else 0
        self.putd(6, 6, [Ann.BIT_DAY_DATE, ['DYDT: %d' % a2dydt, 'DYDT']])
        if a2dydt == 1:        
            w = bcd2int(b & 0x07)
            ws = days_of_week[self.options['fdw']][w - 1]
            self.putd(2, 0, [Ann.BIT_DAY, ['Weekday: %s' % ws, 'WD: %d' % w, 'WD', 'W']])
        else:
            da = bcd2int(b & 0x3f)
            self.putd(5, 0, [Ann.BIT_DATE, ['Date / Date: %d' % da, 'D: %d' % da, 'D']])
        #block
        if self.inblock == 0x0c and self.blockmode == rw :
            if (self.a2m2, self.a2m3, a2m4) == (1, 1, 1):
                d = 'every minute'
            elif (self.a2m2, self.a2m3, a2m4) == (0, 1, 1):
                d = 'every hour, minute=%02d' % self.al2minutes
            elif (self.a2m2, self.a2m3, a2m4) == (0, 0, 1):
                d = 'every day, hh:mm=%02d:%02d%s' % (self.al2hours, self.al2minutes, self.a2ampm)
            elif (self.a2m2, self.a2m3, a2m4) == (0, 0, 0):
                if a2dydt == 1:
                    daydate = ws     
                else:
                    daydate = '%d. of month' % da
                d = 'every %s, hh:mm=%02d:%02d' % (
                    daydate, self.al2hours, self.al2minutes)
            else:
                d = 'invalid setting'  #FIXME: print warning
            d = 'Alarm2: ' + d    
            self.put(self.startreg, self.es, self.out_ann, 
                [Ann.BLOCK_ALARM2, ['%s %s' % (rw, d)]])
        self.inblock = -1

    def handle_reg_0x0e(self, b, rw): # Control Register
        self.putd(7, 0, [Ann.REG_CONTROL, ['Control', 'Ctrl', 'C']])
        eosc = 1 if (b & (1 << 7)) else 0
        bbsqw = 1 if (b & (1 << 6)) else 0
        bbsqw2 = 'en' if (b & (1 << 6)) else 'dis'
        conv = 1 if (b & (1 << 5)) else 0
        intcn2 = 'alarm interrupt' if (b & (1 << 5)) else 'square wave'
        intcn = 1 if (b & (1 << 2)) else 0
        a2ie = 1 if (b & (1 << 1)) else 0
        a2ie2 = 'en' if (b & (1 << 1)) else 'dis'
        a1ie = 1 if (b & 1) else 0
        a1ie2 = 'en' if (b & 1) else 'dis'
        self.putd(7, 7, [Ann.BIT__EOSC, ['Enable oscillator: %d' % eosc,
            'enab osc: %d' % eosc, 'EO: %d' % eosc, 'EO']])
        self.putd(6, 6, [Ann.BIT_BBSQWE, ['Battery backed square wave %sabled' % bbsqw2,
            'BBSQWE: %sabled' % bbsqw2, 'SQWE: %d' % bbsqw, 'S: %d' % bbsqw, 'S']])
        self.putd(5, 5, [Ann.BIT_CONV, ['Forced temperature conversion: %d' % conv,
            'frc tconv: %d' % conv, 'FC: %d' % conv, 'FC']])
        if self.options['subtype'] == 'SN':  # bit 4 and 3 are reserved for DS3231M
            r = rates[((b >> 3) & 0x03)]
            self.putd(4, 3, [Ann.BIT_RATE, ['Square wave output rate: %s' % r,
                'Square wave rate: %s' % r, 'SQW rate: %s' % r, 'Rate: %s' % r,
                'RA: %s' % r, 'RA', 'R']])
        else:
            self.putr(4)
            self.putr(3)
        self.putd(2, 2, [Ann.BIT_INTCN, ['Int/SQW pin: %s' % intcn2,
            'Int on pin: %d' % intcn, 'IP: %d' % intcn, 'IP']])    
        self.putd(1, 1, [Ann.BIT_A2IE, ['Alarm2 interrupt %sabled' % a2ie2,
            'Al2 INT %sabled' % a2ie2, 'Al2 INT: %d' % a2ie, 'A2I: %d' % a2ie, 'A2I']])
        self.putd(0, 0, [Ann.BIT_A1IE, ['Alarm1 interrupt %sabled' % a1ie2,
            'Al1 INT %sabled' % a1ie2, 'Al1 INT: %d' % a1ie, 'A1I: %d' % a1ie, 'A1I']])
        #FIXME: add block output    

    def handle_reg_0x0f(self, b, rw): # Control / Status Register
        self.putd(7, 0, [Ann.REG_CONTROL_STATUS, ['Control / Status', 'Ctrl/Stat', 'C/S']])
        for i in (6, 5, 4):
            self.putr(i)
        osf = 1 if (b & (1 << 7)) else 0
        en32 = 1 if (b & (1 << 3)) else 0
        bsy = 1 if (b & (1 << 2)) else 0
        a2f = 1 if (b & (1 << 1)) else 0
        a1f = 1 if (b & 1) else 0
        self.putd(7, 7, [Ann.BIT_OSF, ['Oscillator stop flag: %d' % osf,
            'Osc stop: %d' % osf, 'OS: %d' % osf, 'OS']])
        self.putd(3, 3, [Ann.BIT_EN32KHZ, ['Enable 32kHz output: %d' % en32,
            'En 32k out: %d' % en32, '32k: %d' % en32, '32k']])
        #FIXME: also for model M?
        self.putd(2, 2, [Ann.BIT_BSY, ['Busy TXCO: %d' % bsy,
            'TXCO: %d' % bsy, 'TX: %d' % bsy, 'TX']])
        self.putd(1, 1, [Ann.BIT_A2F, ['Alarm2 flag: %d' % a2f,
            'Al2 flg: %d' % a2f, 'A2F: %d' % a2f, 'A2']])
        self.putd(0, 0, [Ann.BIT_A1F, ['Alarm1 flag: %d' % a1f,
            'Al1 flg: %d' % a1f, 'A1F: %d' % a1f, 'A1']])
        #FIXME: add block output        

    def handle_reg_0x10(self, b, rw): # Aging / Offset Register
        self.putd(7, 0, [Ann.REG_AGING_OFFSET, ['Aging offset', 'Aging', 'A']])
        ao = b if b < 128 else b - 256 # signed 2's complement
        self.putd(7, 0, [Ann.BIT_AOFS, ['Offset: %d' % ao, 'Ofs: %d' % ao, 'O: %d' % ao, 'O']])

    def handle_reg_0x11(self, b, rw): # MSB of Temperature Register
        self.putd(7, 0, [Ann.REG_TEMPERATURE_MSB, ['Temperature MSB', 'tm', 't']])
        self.tempMSB = b
        tm = b if b < 128 else b - 256  
        self.putd(7, 0, [Ann.BIT_TMSB, ['tempMSB: %d' % tm, 'tm: %d' % tm, 'tm: %d' % tm, 't']])
        self.startreg = self.ss
        self.inblock = 0x11

    def handle_reg_0x12(self, b, rw): # LSB of Temperature Register
        self.putd(7, 0, [Ann.REG_TEMPERATURE_LSB, ['Temperature LSB', 'tl', 't']])
        for i in range(6): self.putr(i)
        tl = b >> 6
        self.putd(7, 6, [Ann.BIT_TLSB, ['tempLSB: %d' % tl, 'tl: %d' % tl, 'tl: %d' % tl, 't']])
        #block
        if self.inblock == 0x11:
            theta = (self.tempMSB << 2) + tl
            if theta >= 512: theta = theta - 1024 
            d = 'Temperature: %.2f' % ( theta / 4 )
            self.put(self.startreg, self.es, self.out_ann, 
                [Ann.BLOCK_TEMPERATURE, ['%s block data: %s' % (rw, d)]])
        self.inblock = -1

    def handle_reg(self, b, rw):
        #print('reg:%s - block%x' % (self.reg, self.inblock))
        #FIXME: catch out of range register, write warning
        if self.reg not in range(0,0x13):
            self.put(self.ss, self.es, self.out_ann,
                 [Ann.WARNING, ['Ignoring out-of-range register 0x%02X' % self.reg]])
            return
        fn = getattr(self, 'handle_reg_0x%02x' % self.reg)
        fn(b, rw)
        # Honor address auto-increment feature of the DS3231. When the
        # address reaches 0x12, it will wrap around to address 0.
        self.reg += 1
        if self.reg > 0x12:
            self.reg = 0

    def is_correct_chip(self, addr):
        if addr == DS3231_I2C_ADDRESS:
            return True
        self.put(self.ss, self.es, self.out_ann,
                 [Ann.WARNING, ['Ignoring non-DS3231 data (slave 0x%02X)' % addr]])
        return False

    def decode(self, ss, es, data):
        cmd, databyte = data

        # Collect the 'BITS' packet, then return. The next packet is
        # guaranteed to belong to these bits we just stored.
        if cmd == 'BITS':
            self.bits = databyte
            return

        # Store the start/end samples of this I²C packet.
        self.ss, self.es = ss, es

        #print('%s - %s' % (self.state, cmd))

        # State machine.
        if self.state == 'IDLE':
            # Wait for an I²C START condition.
            if cmd == 'START':
                self.state = 'GET SLAVE ADDR'
        elif self.state == 'GET SLAVE ADDR':
            # Wait for an address write operation.
            if cmd == 'ADDRESS WRITE':
                if self.is_correct_chip(databyte):
                    self.state = 'GET REG ADDR'
                else:    
                    self.state = 'IDLE'
            # Wait for an address read operation.
            elif cmd == 'ADDRESS READ':
                if self.is_correct_chip(databyte):
                    self.state = 'READ RTC REGS'
                else:    
                    self.state = 'IDLE'
        elif self.state == 'GET REG ADDR':
            # Wait for a data write (master selects the slave register).
            if cmd == 'DATA WRITE':
                self.reg = databyte  #start register for read/write
                self.state = 'WRITE RTC REGS'
            elif cmd == 'STOP':
                self.state = 'IDLE'
        elif self.state == 'WRITE RTC REGS':
            # If we see a Repeated Start here, it's an RTC read.
            if cmd == 'START REPEAT':
                self.state = 'GET SLAVE ADDR'
            # Otherwise: Get data bytes until a STOP condition occurs.
            elif cmd == 'DATA WRITE':
                self.handle_reg(databyte, 'Wrote')
            elif cmd == 'STOP':
                self.state = 'IDLE'
        elif self.state == 'READ RTC REGS':
            if cmd == 'DATA READ':
                self.handle_reg(databyte, 'Read')
            elif cmd == 'STOP':
                self.state = 'IDLE'

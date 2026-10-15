import { describe,expect,it } from 'vitest';
import { minor,money } from './api';
describe('Money entry',()=>{
  it('parses decimal text to exact integer kobo',()=>{expect(minor('25000.01')).toBe(2500001);expect(minor('0.29')).toBe(29);expect(minor('1.1')).toBe(110)});
  it('rejects fractional kobo, scientific notation, and non-positive amounts',()=>{for(const value of ['1.001','1e3','-1','NaN','0','1000001'])expect(()=>minor(value)).toThrow()});
  it('formats naira for Nigerian users',()=>{expect(money(123456)).toContain('1,234.56')});
});

import { describe,expect,it } from 'vitest';
import { render,screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { CircleCard,Simulation } from './ui';
import type { Circle } from '../types';
describe('Financial interface provenance and access',()=>{
  it('shows simulation notice explicitly',()=>{render(<Simulation/>);expect(screen.getByText(/No real money/)).toBeInTheDocument()});
  it('does not show protected balances on discovery cards',()=>{const circle:Circle={id:'test',name:'Study Fund',description:'A shared community goal.',purpose:'Study',university:'Demo University',department:'',visibility:'discoverable',joining_method:'approval',contribution_frequency:'monthly',contribution_target:0,membership_limit:20,member_count:3,membership_status:null,role:null,rules:'Pooled savings',invitations_enabled:true};render(<MemoryRouter><CircleCard circle={circle}/></MemoryRouter>);expect(screen.queryByText(/Available simulated funds/)).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Request to join'})).toBeInTheDocument()});
});

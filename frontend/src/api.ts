import { QueryClient, useQuery } from '@tanstack/react-query';
import type { User } from './types';

export const queryClient = new QueryClient({defaultOptions:{queries:{retry:1,staleTime:15000,refetchOnWindowFocus:true},mutations:{retry:0}}});
export const API_BASE='/api/v1';
export class ApiError extends Error {status:number;requestId?:string;constructor(message:string,status:number,requestId?:string){super(message);this.status=status;this.requestId=requestId}}
let refreshing:Promise<boolean>|null=null;
function csrf(){return document.cookie.split('; ').find(c=>c.startsWith('cc_csrf='))?.split('=')[1]??''}
export async function api<T>(path:string, options:RequestInit={}, retry=true):Promise<T>{
  let response:Response;
  try{response=await fetch(`${API_BASE}${path}`,{...options,credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf(),...options.headers}})}
  catch{throw new ApiError('Cannot reach CampusCircle. Check your connection and try again.',0)}
  if(response.status===401 && retry && !path.startsWith('/auth/')){
    refreshing??=fetch(`${API_BASE}/auth/refresh`,{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf()}}).then(r=>r.ok).catch(()=>false).finally(()=>{refreshing=null});
    if(await refreshing)return api<T>(path,options,false);
  }
  const data:unknown=await response.json().catch(()=>null);
  if(!response.ok){
    const body=data && typeof data==='object' ? data as {detail?:unknown;request_id?:string} : {};
    const detail=body.detail??null;
    const message=typeof detail==='string'?detail:Array.isArray(detail)?detail.map((d: {loc?:string[];msg?:string})=>`${d.loc?.slice(1).join('.')??'Field'}: ${d.msg??'Invalid value'}`).join('. '):'Something went wrong. Please try again.';
    throw new ApiError(response.status>=500&&body.request_id?`${message} (Reference: ${body.request_id.slice(0,8)})`:message,response.status,body.request_id);
  }
  return data as T;
}
export function useApi<T>(path:string, enabled=true, poll?:number){return useQuery({queryKey:[path],queryFn:()=>api<T>(path),enabled,refetchInterval:poll})}
export function useUser(){return useQuery({queryKey:['/me'],queryFn:()=>api<User>('/me'),retry:false,staleTime:60000})}
export function post<T>(path:string,body:unknown={},key?:string){return api<T>(path,{method:'POST',body:JSON.stringify(body),headers:key?{'Idempotency-Key':key}:undefined})}
export function minor(value:string):number{
  if(!/^\d+(\.\d{1,2})?$/.test(value))throw new Error('Enter a positive amount with at most two decimal places.');
  const [whole,fraction='']=value.split('.');
  const amount=Number(whole)*100+Number(fraction.padEnd(2,'0'));
  if(!Number.isSafeInteger(amount)||amount<=0||amount>100_000_000)throw new Error('Enter an amount between ₦0.01 and ₦1,000,000.');
  return amount;
}
export function money(value:number){return new Intl.NumberFormat('en-NG',{style:'currency',currency:'NGN',minimumFractionDigits:2,maximumFractionDigits:2}).format(value/100)}
export function date(value:string,full=false){return new Intl.DateTimeFormat('en-NG',{day:'numeric',month:'short',...(full?{year:'numeric' as const,hour:'numeric' as const,minute:'2-digit' as const}:{})}).format(new Date(value.endsWith('Z')||/[+-]\d\d:\d\d$/.test(value)?value:`${value}Z`))}
export function title(value:string){return value.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase())}
export function invalidate(){return queryClient.invalidateQueries({predicate:q=>q.queryKey[0]!=='/me'})}

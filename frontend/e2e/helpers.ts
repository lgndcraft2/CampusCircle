import { expect, type APIRequestContext } from '@playwright/test';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

export const ORIGIN = 'http://127.0.0.1:5183';
export const PASSWORD = 'e2e-password-strong-1';
const OUTBOX = join(process.cwd(), '..', '.dev-mail');

/** Controlled test mechanism: read the newest code from the private development outbox (never exposed over HTTP). */
export async function codeFor(email: string, kind: 'Verify' | 'Reset' = 'Verify'): Promise<string> {
  for (let attempt = 0; attempt < 20; attempt++) {
    const files = readdirSync(OUTBOX).map(f => join(OUTBOX, f)).sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
    for (const file of files) {
      const mail = JSON.parse(readFileSync(file, 'utf-8')) as { to: string; subject: string; body: string };
      if (mail.to === email && mail.subject.includes(kind)) return /\b(\d{6})\b/.exec(mail.body)![1];
    }
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error(`No ${kind} email for ${email}`);
}

export const unique = (name: string) => `${name}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}@students.example.edu`;

export class Student {
  csrf = '';
  constructor(public api: APIRequestContext, public email: string) {}
  headers(extra: Record<string, string> = {}) { return { 'X-CSRF-Token': this.csrf, Origin: ORIGIN, ...extra }; }
  async get<T>(path: string, status = 200): Promise<T> {
    const r = await this.api.get(`/api/v1${path}`); expect(r.status(), `${path}: ${await r.text()}`).toBe(status); return r.json() as Promise<T>;
  }
  async post<T>(path: string, data: unknown = {}, key?: string, status = 200): Promise<T> {
    const r = await this.api.post(`/api/v1${path}`, { data, headers: this.headers(key ? { 'Idempotency-Key': key } : {}) });
    expect([status, 201]).toContain(r.status()); return r.json() as Promise<T>;
  }
}

/** Register, verify through the outbox, and sign in using the real API. */
export async function apiStudent(api: APIRequestContext, name: string, department = 'Computer Science'): Promise<Student> {
  const email = unique(name.split(' ')[0].toLowerCase());
  const reg = await api.post('/api/v1/auth/register', { data: { full_name: name, phone: '+2348031234567', university: 'Demo University', department, email, password: PASSWORD }, headers: { Origin: ORIGIN } });
  expect(reg.status(), await reg.text()).toBe(201);
  const verify = await api.post('/api/v1/auth/verify', { data: { email, code: await codeFor(email) }, headers: { Origin: ORIGIN } });
  expect(verify.status()).toBe(200);
  const login = await api.post('/api/v1/auth/login', { data: { email, password: PASSWORD }, headers: { Origin: ORIGIN } });
  expect(login.status()).toBe(200);
  const student = new Student(api, email);
  student.csrf = (await api.storageState()).cookies.find(c => c.name === 'cc_csrf')!.value;
  return student;
}

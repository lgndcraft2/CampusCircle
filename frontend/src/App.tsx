import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router-dom';
import { Loading } from './components/ui';
import { AuthPage, Landing, Legal, NotFound } from './pages/Public';
import { DashboardPage, GuidePage, InvitePage, NotificationsPage, ProfilePage, Workspace } from './pages/Workspace';

const CirclesPage=lazy(()=>import('./pages/Circles').then(m=>({default:m.CirclesPage})));
const CircleDetailPage=lazy(()=>import('./pages/Circles').then(m=>({default:m.CircleDetailPage})));
const LoanDetailPage=lazy(()=>import('./pages/Loans').then(m=>({default:m.LoanDetailPage})));
const ChatPage=lazy(()=>import('./pages/Chat').then(m=>({default:m.ChatPage})));

export default function App(){return <Suspense fallback={<Loading/>}><Routes><Route path="/" element={<Landing/>}/><Route path="/login" element={<AuthPage mode="login"/>}/><Route path="/register" element={<AuthPage mode="register"/>}/><Route path="/verify" element={<AuthPage mode="verify"/>}/><Route path="/forgot-password" element={<AuthPage mode="forgot-password"/>}/><Route path="/reset-password" element={<AuthPage mode="reset-password"/>}/><Route path="/terms" element={<Legal/>}/><Route path="/privacy" element={<Legal privacy/>}/><Route path="/invite/:token" element={<InvitePage/>}/><Route path="/app" element={<Workspace/>}><Route index element={<DashboardPage/>}/><Route path="circles" element={<CirclesPage/>}/><Route path="circles/:circleId" element={<CircleDetailPage/>}/><Route path="loans/:loanId" element={<LoanDetailPage/>}/><Route path="chat" element={<ChatPage/>}/><Route path="profile" element={<ProfilePage/>}/><Route path="notifications" element={<NotificationsPage/>}/><Route path="guide" element={<GuidePage/>}/></Route><Route path="*" element={<NotFound/>}/></Routes></Suspense>}

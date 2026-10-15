import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { Toaster } from 'sonner';
import { queryClient } from './api';
import App from './App';
import './styles.css';

class ErrorBoundary extends React.Component<{children:React.ReactNode},{failed:boolean}>{
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true}}
  render(){return this.state.failed?<main className="standalone"><h1>Something interrupted CampusCircle.</h1><p>Reload the page to reconnect to your saved data.</p><button className="btn primary" onClick={()=>window.location.reload()}>Reload</button></main>:this.props.children}
}
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><ErrorBoundary><QueryClientProvider client={queryClient}><BrowserRouter><App/><Toaster richColors position="top-right"/></BrowserRouter></QueryClientProvider></ErrorBoundary></React.StrictMode>);

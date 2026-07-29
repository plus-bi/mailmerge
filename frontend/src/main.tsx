import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

type Campaign = {id:string; name:string; purpose:string; state:string; subject_template:string};
const token = new URLSearchParams(location.hash.slice(1)).get('token') || localStorage.getItem('mailmerge-token') || '';
if (token) localStorage.setItem('mailmerge-token', token);
const api = async (path:string, init:RequestInit={}) => {
  const response = await fetch('/api/v1'+path, {...init, headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json',...(init.headers||{})}});
  if (!response.ok) throw new Error(await response.text());
  return response.json();
};

function App(){
  const [campaigns,setCampaigns]=useState<Campaign[]>([]), [selected,setSelected]=useState<Campaign|null>(null), [error,setError]=useState('');
  const load=()=>api('/campaigns').then(setCampaigns).catch(e=>setError(String(e)));
  useEffect(()=>{void load();},[]);
  const create=async()=>{const name=prompt('Campaign name'); if(!name)return; await api('/campaigns',{method:'POST',body:JSON.stringify({name})}); load();};
  const preflight=async()=>{if(!selected)return; const result=await api(`/campaigns/${selected.id}/preflight`,{method:'POST'}); alert(result.ok?`${result.previews.length} messages ready`:result.errors.join('\n'));};
  if(!token)return <main><h1>Local Mail Merge</h1><p>Launch the application using its desktop launcher, or add the session token as <code>#token=…</code>.</p></main>;
  return <main><header><div><h1>Local Mail Merge</h1><p>Private, local campaign delivery</p></div><button onClick={create}>New campaign</button></header>
    {error&&<div className="error">{error}</div>}<section className="layout"><nav>{campaigns.map(c=><button className={selected?.id===c.id?'selected':''} onClick={()=>setSelected(c)} key={c.id}><strong>{c.name}</strong><small>{c.purpose} · {c.state}</small></button>)}</nav>
    <article>{selected?<><h2>{selected.name}</h2><dl><dt>Status</dt><dd>{selected.state}</dd><dt>Subject</dt><dd>{selected.subject_template||'Not set'}</dd></dl><button onClick={preflight}>Run preflight</button></>:<p>Select a campaign to inspect it.</p>}</article></section>
    <footer>No open or click tracking.</footer></main>;
}
createRoot(document.getElementById('root')!).render(<App/>);

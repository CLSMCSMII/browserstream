"use strict";

const state = {config:null, room:null, socket:null, pc:null, stream:null, debug:false, pendingCandidates:[]};
const el = id => document.getElementById(id);
const show = id => { ['home','presenter','kiosk'].forEach(x => el(x).hidden = x !== id); };
const roomByID = id => state.config.rooms.find(r => r.id === id);
const baseURL = () => (state.config.public_url || window.location.origin).replace(/\/$/, '');
const wsURL = path => `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${path}`;
function log(message) { console.log(message); if (state.debug) { el('debug').hidden=false; el('debug').textContent += `${new Date().toISOString()} ${message}\n`; } }
function fail(message) { log(message); alert(message); }
function send(type, value='', sessionID='') { if (state.socket && state.socket.readyState===WebSocket.OPEN) state.socket.send(JSON.stringify({Type:type,Value:value,SessionID:sessionID})); }
function iceOptions() { return {iceServers: state.config.ice_servers || [], iceTransportPolicy: state.config.ice_transport_policy || 'all', iceCandidatePoolSize:10}; }
function applyICEConfig(value){const c=JSON.parse(value);state.config.ice_servers=c.ice_servers||[];state.config.ice_transport_policy=c.ice_transport_policy||'all';}
async function addRemoteCandidate(value){const candidate=JSON.parse(value);if(state.pc&&state.pc.remoteDescription)await state.pc.addIceCandidate(candidate);else state.pendingCandidates.push(candidate);}
async function flushRemoteCandidates(){while(state.pc&&state.pc.remoteDescription&&state.pendingCandidates.length)await state.pc.addIceCandidate(state.pendingCandidates.shift());}
function setRoomText() { el('room-text').textContent = state.room ? state.room.label : ''; }
function clearPeer() { state.pendingCandidates=[]; if(state.pc){state.pc.close();state.pc=null;} if(state.stream){state.stream.getTracks().forEach(t=>t.stop());state.stream=null;} el('video').srcObject=null; document.body.classList.remove('playing'); }

async function loadConfig() {
  const response = await fetch('/api/config',{cache:'no-store'});
  if (!response.ok) throw new Error(`configuration request failed (${response.status})`);
  state.config = await response.json();
  state.debug = state.config.debug === true && new URL(location.href).searchParams.get('debug') === '1';
  document.title = state.config.app_name;
  el('brand').textContent = state.config.app_name;
  el('heading').textContent = `${state.config.app_name} screen sharing`;
  el('kiosk-brand').textContent = state.config.app_name;
  el('public-url').textContent = baseURL();
  const buttons=el('room-buttons'); buttons.replaceChildren();
  state.config.rooms.forEach(room => { const b=document.createElement('button'); b.type='button';b.className='btn btn-dark btn-block btn-lg room-button';b.textContent=room.label;b.addEventListener('click',()=>openPresenter(room.id));buttons.appendChild(b); });
}

function openPresenter(roomID) {
  const room=roomByID(roomID); if(!room)return fail('Unknown room.');
  state.room=room;setRoomText(); history.replaceState(null,'',`/?present=1&room=${encodeURIComponent(room.id)}`);show('presenter');el('verification-code').focus();
}

function presenterFailure(message) {
  if (state.stream) { state.stream.getTracks().forEach(track=>track.stop()); state.stream=null; }
  if (state.socket) { state.socket.close(); state.socket=null; }
  el('start-share').disabled=false;
  document.body.classList.remove('presenter-mode','playing');
  show('presenter');
  fail(message);
}
async function captureAndStart() {
  try { state.stream=await navigator.mediaDevices.getDisplayMedia({video:true,audio:true}); }
  catch(e){ return presenterFailure(`Screen capture failed: ${e.message||e}`); }
  el('video').srcObject=state.stream; show('kiosk'); document.body.classList.add('presenter-mode','playing');
  state.stream.getVideoTracks().forEach(t=>t.addEventListener('ended',()=>location.assign(baseURL())));
  send('startSession');
}
async function startSharing() {
  const code=el('verification-code').value.trim().toUpperCase();
  if(!/^[A-Z0-9]{6}$/.test(code))return fail('Enter the six-character code shown on the display.');
  el('start-share').disabled=true;
  state.socket=new WebSocket(wsURL(`/ws_present?id=${encodeURIComponent(state.room.id)}`));
  state.socket.addEventListener('open',()=>send('auth',code));
  state.socket.addEventListener('message',async event=>{
    let m;try{m=JSON.parse(event.data);}catch{return;}
    if(m.Type==='iceConfig')applyICEConfig(m.Value);
    else if(m.Type==='authAccepted')await captureAndStart();
    else if(m.Type==='newSession')await presenterPeer(m.SessionID);
    else if(m.Type==='addCalleeIceCandidate')await addRemoteCandidate(m.Value);
    else if(m.Type==='gotAnswer'&&state.pc){await state.pc.setRemoteDescription(JSON.parse(m.Value));await flushRemoteCandidates();}
    else if(m.Type==='invalidCode')presenterFailure('Invalid verification code.');
    else if(m.Type==='lockedOut')presenterFailure('Too many failed attempts. Try again later.');
    else if(m.Type==='displayNotFound')presenterFailure('The display is not online.');
    else if(m.Type==='presenterBusy')presenterFailure('Another presenter is already connected.');
  });
  state.socket.addEventListener('close',()=>{const wasSharing=!!state.stream;state.socket=null;el('start-share').disabled=false;log('signaling connection closed');if(wasSharing)presenterFailure('The display or signaling connection went offline.');});
}
async function presenterPeer(sessionID) {
  state.pc=new RTCPeerConnection(iceOptions());
  state.pc.onicecandidate=e=>{if(e.candidate)send('addCallerIceCandidate',JSON.stringify(e.candidate),sessionID);};
  state.stream.getTracks().forEach(track=>state.pc.addTrack(track,state.stream));
  const offer=await state.pc.createOffer();await state.pc.setLocalDescription(offer);send('gotOffer',JSON.stringify(offer),sessionID);
}

function displayToken() {
  const hash=new URLSearchParams(location.hash.replace(/^#/,'')); const token=hash.get('token')||localStorage.getItem(`display-token:${state.room.id}`)||'';
  if(token)localStorage.setItem(`display-token:${state.room.id}`,token); history.replaceState(null,'',location.pathname+location.search); return token;
}
function openDisplay(roomID) {
  const room=roomByID(roomID);if(!room){el('startup-error').hidden=false;el('startup-error').textContent='Unknown room.';return;}
  state.room=room;setRoomText();show('kiosk');document.body.classList.add('kiosk-display');
  const presenterURL=`${baseURL()}/?present=1&room=${encodeURIComponent(room.id)}`;
  el('qrcode').replaceChildren(); new QRCode(el('qrcode'),{text:presenterURL,width:180,height:180,correctLevel:QRCode.CorrectLevel.M});
  const token=displayToken();if(!token){el('kiosk-help').textContent='Display enrollment token is missing. See the installation documentation.';return;}
  state.socket=new WebSocket(wsURL(`/ws_display?id=${encodeURIComponent(room.id)}`));state.socket.addEventListener('open',()=>send('auth',token));
  state.socket.addEventListener('message',async event=>{let m;try{m=JSON.parse(event.data);}catch{return;}if(m.Type==='iceConfig'){applyICEConfig(m.Value);}else if(m.Type==='displayReady'){el('kiosk-code').textContent=m.SessionID;}else if(m.Type==='refreshCode'){el('kiosk-code').textContent=m.Value;}else if(m.Type==='newSession'){clearPeer();await displayPeer(m.SessionID);}else if(m.Type==='addCallerIceCandidate'){await addRemoteCandidate(m.Value);}else if(m.Type==='gotOffer'&&state.pc)await answerOffer(m.SessionID,JSON.parse(m.Value));else if(m.Type==='presenterClosed'){clearPeer();el('kiosk-code').textContent=m.Value;}else if(m.Type==='unauthorized'){el('kiosk-help').textContent='Display enrollment failed.';}});
}
async function displayPeer(sessionID) {
  state.pc=new RTCPeerConnection(iceOptions());state.stream=new MediaStream();el('video').srcObject=state.stream;
  state.pc.onicecandidate=e=>{if(e.candidate)send('addCalleeIceCandidate',JSON.stringify(e.candidate),sessionID);};
  state.pc.ontrack=e=>{state.stream.addTrack(e.track);el('video').play().then(()=>document.body.classList.add('playing')).catch(err=>log(`autoplay: ${err}`));e.track.addEventListener('ended',()=>{if(e.track.kind==='video')clearPeer();});};
}
async function answerOffer(sessionID,offer){await state.pc.setRemoteDescription(offer);await flushRemoteCandidates();const answer=await state.pc.createAnswer();await state.pc.setLocalDescription(answer);send('gotAnswer',JSON.stringify(answer),sessionID);}

function route(){const u=new URL(location.href);if(u.pathname.startsWith('/room/'))return openDisplay(decodeURIComponent(u.pathname.slice(6)));if(u.searchParams.get('present')==='1')return openPresenter(u.searchParams.get('room'));show('home');}
el('verification-code').addEventListener('input',e=>{e.target.value=e.target.value.toUpperCase().replace(/[^A-Z0-9]/g,'').slice(0,6);});el('start-share').addEventListener('click',startSharing);document.querySelectorAll('.cancel').forEach(b=>b.addEventListener('click',()=>location.assign(baseURL())));
loadConfig().then(route).catch(err=>{el('startup-error').hidden=false;el('startup-error').textContent=err.message;});

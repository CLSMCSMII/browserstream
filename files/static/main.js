"use strict";

const PRODUCT_NAME = 'AwareStream';
const KIOSK_NAME = 'AwareStream';
const state = {config:null, room:null, socket:null, pc:null, stream:null, captureStream:null, audioContext:null, audioGain:null, debug:false, pendingCandidates:[]};
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
function setRoomText() { el('room-text').textContent = state.room ? state.room.label : ''; el('kiosk-location').textContent = state.room ? state.room.label : ''; }
function stopMedia() {
  if(state.stream){state.stream.getTracks().forEach(t=>t.stop());state.stream=null;}
  if(state.captureStream){state.captureStream.getTracks().forEach(t=>t.stop());state.captureStream=null;}
  if(state.audioContext){state.audioContext.close().catch(()=>{});state.audioContext=null;}
  state.audioGain=null;
}
function clearPeer() { state.pendingCandidates=[];if(state.pc){state.pc.close();state.pc=null;}stopMedia();el('video').srcObject=null;el('enable-audio').hidden=true;document.body.classList.remove('playing'); }

async function prepareOutgoingStream(captured, shareAudio, volume) {
  state.captureStream=captured;
  const outgoing=new MediaStream(captured.getVideoTracks());
  const audioTracks=captured.getAudioTracks();
  if(!shareAudio||audioTracks.length===0)return outgoing;
  const AudioContextClass=window.AudioContext||window.webkitAudioContext;
  if(!AudioContextClass){audioTracks.forEach(track=>outgoing.addTrack(track));return outgoing;}
  state.audioContext=new AudioContextClass();
  const source=state.audioContext.createMediaStreamSource(new MediaStream(audioTracks));
  state.audioGain=state.audioContext.createGain();
  const destination=state.audioContext.createMediaStreamDestination();
  state.audioGain.gain.value=volume;
  source.connect(state.audioGain).connect(destination);
  destination.stream.getAudioTracks().forEach(track=>outgoing.addTrack(track));
  if(state.audioContext.state==='suspended')await state.audioContext.resume();
  return outgoing;
}

async function loadConfig() {
  const response = await fetch('/api/config',{cache:'no-store'});
  if (!response.ok) throw new Error(`configuration request failed (${response.status})`);
  state.config = await response.json();
  state.debug = state.config.debug === true && new URL(location.href).searchParams.get('debug') === '1';
  document.title = PRODUCT_NAME;
  el('brand').textContent = PRODUCT_NAME;
  el('kiosk-brand').textContent = KIOSK_NAME;
  el('public-url').textContent = new URL(baseURL()).host;
  const buttons=el('room-buttons'); buttons.replaceChildren();
  state.config.rooms.forEach(room => { const b=document.createElement('button'); b.type='button';b.className='btn btn-dark btn-block btn-lg room-button';b.textContent=room.label;b.addEventListener('click',()=>openPresenter(room.id));buttons.appendChild(b); });
}

function openPresenter(roomID) {
  const room=roomByID(roomID); if(!room)return fail('Unknown room.');
  state.room=room;setRoomText(); history.replaceState(null,'',`/?present=1&room=${encodeURIComponent(room.id)}`);show('presenter');el('verification-code').focus();
}

function presenterFailure(message) {
  stopMedia();
  if (state.socket) { state.socket.close(); state.socket=null; }
  el('start-share').disabled=false;
  document.body.classList.remove('presenter-mode','playing');
  show('presenter');
  fail(message);
}
async function startSharing() {
  const code=el('verification-code').value.trim().toUpperCase();
  if(!/^[A-Z0-9]{6}$/.test(code))return fail('Enter the six-character code shown on the display.');
  el('start-share').disabled=true;
  const shareAudio=el('share-audio').checked;
  const volume=Number(el('audio-volume').value)/100;
  let captured;
  try { captured=await navigator.mediaDevices.getDisplayMedia({video:true,audio:shareAudio});state.stream=await prepareOutgoingStream(captured,shareAudio,volume); }
  catch(e){ return presenterFailure(`Screen capture failed: ${e.message||e}`); }
  el('video').muted=true;el('video').srcObject=state.stream;el('stream-audio-enabled').checked=shareAudio;el('stream-audio-volume').value=el('audio-volume').value;el('stream-audio-volume-value').textContent=`${el('audio-volume').value}%`;show('kiosk');document.body.classList.add('presenter-mode','playing');
  state.stream.getVideoTracks().forEach(t=>t.addEventListener('ended',()=>location.assign(baseURL())));
  state.socket=new WebSocket(wsURL(`/ws_present?id=${encodeURIComponent(state.room.id)}`));
  state.socket.addEventListener('open',()=>send('auth',code));
  state.socket.addEventListener('message',async event=>{
    let m;try{m=JSON.parse(event.data);}catch{return;}
    if(m.Type==='iceConfig')applyICEConfig(m.Value);
    else if(m.Type==='authAccepted')send('startSession');
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
function showDisplayCode(code) { el('kiosk-code').textContent=code; el('kiosk-help').textContent=''; }
function showDisplayConnecting() { el('kiosk-code').textContent='------'; el('kiosk-help').textContent='Connecting to presenter…'; }
function openDisplay(roomID) {
  const room=roomByID(roomID);if(!room){el('startup-error').hidden=false;el('startup-error').textContent='Unknown room.';return;}
  state.room=room;setRoomText();show('kiosk');document.body.classList.add('kiosk-display');
  const token=displayToken();if(!token){el('kiosk-help').textContent='Display enrollment token is missing. See the installation documentation.';return;}
  state.socket=new WebSocket(wsURL(`/ws_display?id=${encodeURIComponent(room.id)}`));state.socket.addEventListener('open',()=>send('auth',token));
  state.socket.addEventListener('message',async event=>{let m;try{m=JSON.parse(event.data);}catch{return;}if(m.Type==='iceConfig'){applyICEConfig(m.Value);}else if(m.Type==='displayReady'){showDisplayCode(m.SessionID);}else if(m.Type==='refreshCode'){showDisplayConnecting();}else if(m.Type==='newSession'){clearPeer();showDisplayConnecting();await displayPeer(m.SessionID);}else if(m.Type==='addCallerIceCandidate'){await addRemoteCandidate(m.Value);}else if(m.Type==='gotOffer'&&state.pc)await answerOffer(m.SessionID,JSON.parse(m.Value));else if(m.Type==='presenterClosed'){clearPeer();showDisplayCode(m.Value);}else if(m.Type==='unauthorized'){el('kiosk-help').textContent='Display enrollment failed.';}});
}
async function displayPeer(sessionID) {
  state.pc=new RTCPeerConnection(iceOptions());state.stream=new MediaStream();const video=el('video');video.muted=false;video.srcObject=state.stream;
  state.pc.onicecandidate=e=>{if(e.candidate)send('addCalleeIceCandidate',JSON.stringify(e.candidate),sessionID);};
  state.pc.ontrack=e=>{state.stream.addTrack(e.track);video.play().then(()=>{document.body.classList.add('playing');el('enable-audio').hidden=true;}).catch(err=>{log(`audible autoplay blocked: ${err}`);video.muted=true;video.play().then(()=>document.body.classList.add('playing')).catch(playErr=>log(`autoplay: ${playErr}`));el('enable-audio').hidden=false;});e.track.addEventListener('ended',()=>{if(e.track.kind==='video')clearPeer();});};
}
async function answerOffer(sessionID,offer){await state.pc.setRemoteDescription(offer);await flushRemoteCandidates();const answer=await state.pc.createAnswer();await state.pc.setLocalDescription(answer);send('gotAnswer',JSON.stringify(answer),sessionID);}

function route(){const u=new URL(location.href);if(u.pathname.startsWith('/room/'))return openDisplay(decodeURIComponent(u.pathname.slice(6)));if(u.searchParams.get('present')==='1')return openPresenter(u.searchParams.get('room'));show('home');}
el('verification-code').addEventListener('input',e=>{e.target.value=e.target.value.toUpperCase().replace(/[^A-Z0-9]/g,'').slice(0,6);});
el('share-audio').addEventListener('change',e=>{el('audio-volume').disabled=!e.target.checked;});
el('audio-volume').addEventListener('input',e=>{el('audio-volume-value').textContent=`${e.target.value}%`;});
el('stream-audio-enabled').addEventListener('change',e=>{state.stream?.getAudioTracks().forEach(track=>track.enabled=e.target.checked);});
el('stream-audio-volume').addEventListener('input',e=>{const value=Number(e.target.value)/100;if(state.audioGain)state.audioGain.gain.value=value;el('stream-audio-volume-value').textContent=`${e.target.value}%`;});
el('enable-audio').addEventListener('click',()=>{const video=el('video');video.muted=false;video.play().then(()=>{el('enable-audio').hidden=true;}).catch(err=>log(`enable audio: ${err}`));});
el('start-share').addEventListener('click',startSharing);document.querySelectorAll('.cancel').forEach(b=>b.addEventListener('click',()=>location.assign(baseURL())));
loadConfig().then(route).catch(err=>{el('startup-error').hidden=false;el('startup-error').textContent=err.message;});

/* Observe and plan against the original game runtime. Inputs are sent by the browser driver. */
'use strict';
globalThis.createFanren3DBridge = function ({runtime, ownerCheck}) {
  if (!runtime || typeof ownerCheck !== 'function') throw Error('Runtime and owner check are required');
  function requireOwner() {if (ownerCheck() !== true) throw Error('identity_changed');}
  function stats() {return runtime.stats();}
  function planStep(x,z,radius=.35) {
    requireOwner();
    if (![x,z,radius].every(Number.isFinite) || radius < .2) throw Error('invalid_movement_bounds');
    const state=stats(), [px,,pz]=state.actorPosition, dx=x-px,dz=z-pz,distance=Math.hypot(dx,dz);
    if (state.controlMode!=='companion'||!state.companion?.modelReady) return {ready:false,reason:'actor_unavailable'};
    if (distance<=radius) return {ready:true,reached:true,position:state.actorPosition,distance,keys:[]};
    const yaw=state.orbitBasis??state.yaw,forward=-Math.sin(yaw)*dx-Math.cos(yaw)*dz,right=Math.cos(yaw)*dx-Math.sin(yaw)*dz;
    const scale=Math.max(Math.abs(forward),Math.abs(right)),keys=[];
    if (Math.abs(forward)>scale*.45) keys.push(forward>0?'w':'s');
    if (Math.abs(right)>scale*.45) keys.push(right>0?'d':'a');
    return {ready:true,reached:false,position:state.actorPosition,distance,keys};
  }
  return Object.freeze({stats,planStep,requireOwner});
};

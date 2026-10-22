/* S5: real API-backed map, read-only playback, and shared events. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const names = {replan: 'OR-Tools re-plan', cheapest_insertion: 'Cheapest insertion', do_nothing: 'Do nothing'};
  const palette = ['#6be4c1','#79a9ff','#eab96e','#bf9aff','#f38eaf','#71d7e9','#ccdc7b','#ee9b72','#82cbb0','#96a0ed'];
  const statusColours = {pending:'#f4bc66',servicing:'#b7a4ff',served:'#65e2bd',unassigned:'#ff7f88'};
  let frame = null, targetTime = 0, busy = false, playing = false, timer = null;
  let requestNumber = 0, controller = null, debounce = null, placing = false, preview = null;
  let plotReady = false, renderQueue = Promise.resolve(), renderNumber = 0;
  const fmt = (v, digits=1) => Number(v).toLocaleString(undefined, {maximumFractionDigits:digits, minimumFractionDigits:digits});
  const runtime = seconds => seconds < .01 ? `${fmt(seconds*1000, 2)} ms` : `${fmt(seconds, 3)} s`;
  const selected = () => $('strategy').value;
  const history = () => frame && targetTime < frame.committed_time;
  const canEvent = () => frame && !busy && !history() && Math.abs(frame.time-targetTime) < 1e-8;
  function message(text, error=false) { $('message').textContent=text; $('message').classList.toggle('error',error); }
  function pause() { playing=false; clearTimeout(timer); $('play').textContent='▶ Play'; $('play').setAttribute('aria-label','Play simulation'); }
  function controls() {
    $('load').disabled=busy; $('dataset').disabled=busy;
    $('time-slider').disabled=busy || !frame; $('play').disabled=busy || !frame;
    $('strategy').disabled=busy; $('speed').disabled=busy;
    $('break').disabled=!canEvent() || !$('vehicle-select').value;
    $('vehicle-select').disabled=!canEvent(); $('place').disabled=!canEvent();
    $('order-fields').disabled=!canEvent(); $('jump-live').hidden=!history();
    $('timeline-status').textContent=!frame ? 'Viewing the initial plan' : history()
      ? `Historical view · new events available from t=${fmt(frame.committed_time,2)}`
      : `Read-only playback · latest committed event at t=${fmt(frame.committed_time,2)}`;
  }
  function setBusy(value, label='Comparing recovery strategies…') {
    busy=value; if(value) {pause(); cancelRead(); placing=false; $('place').classList.remove('active');}
    $('busy-overlay').hidden=!value; $('busy-label').textContent=label; controls();
  }
  function cancelRead() { requestNumber++; if(controller) controller.abort(); clearTimeout(debounce); }
  async function api(path, options={}) {
    const response=await fetch(path, options);
    let body;
    try {body=await response.json();} catch {throw new Error(`Server returned ${response.status}. Check the running terminal.`);}
    if(!response.ok) {
      let detail=body.detail;
      if(Array.isArray(detail)) detail=detail.map(x=>`${x.loc.slice(1).join('.')}: ${x.msg}`).join('; ');
      const error=new Error(detail || `Request failed (${response.status})`); error.status=response.status; throw error;
    }
    return body;
  }
  function setTime(t) {targetTime=Number(t); $('time-slider').value=targetTime; $('time-value').textContent=fmt(targetTime,2); controls();}
  async function fetchFrame(t) {
    const id=++requestNumber; if(controller) controller.abort(); controller=new AbortController();
    try {
      const data=await api(`/state?t=${encodeURIComponent(t)}`,{signal:controller.signal});
      if(id!==requestNumber || busy) return false;
      await accept(data); return true;
    } catch(error) {if(error.name!=='AbortError' && id===requestNumber){pause();message(error.message,true);} return false;}
  }
  async function accept(data) {
    frame=data; targetTime=data.time;
    $('dataset').value=data.dataset; $('time-slider').max=data.max_time; $('time-slider').step='0.25';
    $('time-max').textContent=fmt(data.max_time,0); setTime(data.time);
    $('session-badge').textContent=`${data.dataset} · ${data.kpis.replan.total_orders} customers · ${data.event_count} total events`;
    $('map-empty').hidden=true; updateVehicles(); updateMetrics(); updateEvents(); controls();
    await drawMap();
  }
  function updateVehicles() {
    if(!frame) return;
    const old=$('vehicle-select').value;
    const vehicles=frame.strategies[selected()].vehicles.filter(v=>v.status!=='broken')
      .sort((a,b)=>Number(b.pending_count>0)-Number(a.pending_count>0) || a.id-b.id);
    $('vehicle-select').replaceChildren(...vehicles.map(v=>new Option(`Vehicle ${v.id} · ${v.status} · ${v.pending_count} pending`,v.id)));
    if(vehicles.some(v=>String(v.id)===old)) $('vehicle-select').value=old;
    if(!vehicles.length) $('vehicle-select').replaceChildren(new Option('No healthy vehicles',''));
    vehicleDetail();
  }
  function vehicleDetail() {
    const v=frame?.strategies[selected()].vehicles.find(v=>String(v.id)===$('vehicle-select').value);
    $('vehicle-detail').textContent=v ? `Used capacity ${v.used_capacity}/${v.capacity} · ${v.served_count} delivered · ${fmt(v.distance_travelled)} travelled` : 'No healthy vehicle available.';
    controls();
  }
  function updateMetrics() {
    const strategy=selected(), k=frame?.kpis[strategy], event=frame?.latest_event;
    $('kpi-heading').textContent=names[strategy];
    $('delta-context').textContent=event ? `Deltas: immediately before → after latest event at t=${fmt(event.time,2)}` : 'Deltas appear after an event';
    const cards=[['distance','Total distance',k?fmt(k.distance):'—'],['dropped_orders','Unassigned orders',k?k.dropped_orders:'—'],['late_deliveries','Late arrivals',k?k.late_deliveries:'—'],['solve_time_sec',event?'Latest recovery time':'Initial solve time',k?runtime(k.solve_time_sec):'—']];
    $('cards').replaceChildren(...cards.map(([key,label,value])=>{
      const card=document.createElement('div'); card.className='kpi-card';
      const title=document.createElement('div'); title.className='kpi-label';title.textContent=label;
      const number=document.createElement('div');number.className='kpi-value';number.textContent=value;
      const detail=document.createElement('div');detail.className='kpi-detail';
      const values=event?.strategies[strategy];
      if(values) {const d=values.delta[key];detail.textContent=`${key==='solve_time_sec'?runtime(values.before[key]):fmt(values.before[key],key==='distance'?1:0)} → ${key==='solve_time_sec'?runtime(values.after[key]):fmt(values.after[key],key==='distance'?1:0)} (${d>=0?'+':''}${fmt(d,key==='solve_time_sec'?3:key==='distance'?1:0)}${key==='solve_time_sec'?' s':''})`; detail.classList.toggle('changed',Math.abs(d)>.00001);}
      else detail.textContent=key==='distance' && k ? `${fmt(k.distance_travelled)} travelled · ${fmt(k.remaining_distance)} remaining` : 'No event comparison yet';
      card.append(title,number,detail);return card;
    }));
    if(!frame) return;
    $('comparison-body').replaceChildren(...Object.keys(names).map(name=>{
      const value=frame.kpis[name], tr=document.createElement('tr');tr.classList.toggle('selected',name===strategy);
      [names[name],`${value.served_orders}/${value.total_orders}`,fmt(value.distance),value.dropped_orders,value.late_deliveries,runtime(value.solve_time_sec)].forEach(text=>{const cell=document.createElement('td');cell.textContent=text;tr.append(cell);});return tr;
    }));
  }
  function updateEvents() {
    $('event-count').textContent=`${frame.events.length} EVENTS`;
    $('event-log').replaceChildren();
    if(!frame.events.length) {const li=document.createElement('li');li.className='muted';li.textContent='No disruptions in this view. Try a breakdown around t=90.';$('event-log').append(li);return;}
    frame.events.slice().reverse().forEach(event=>{
      const li=document.createElement('li'), time=document.createElement('span'), note=document.createElement('small');
      time.className='event-time';time.textContent=`t=${fmt(event.time,2)}`;
      li.append(time,document.createTextNode(event.type==='break_vehicle'?`Vehicle ${event.payload.vehicle_id} broke down`:`Order ${event.payload.id} added at (${fmt(event.payload.x)}, ${fmt(event.payload.y)})`));
      note.textContent=Object.entries(event.strategies).map(([name,v])=>`${names[name]}: ${v.after.dropped_orders} unassigned${v.status==='SOLVE_FAILED'?' · solve failed':''}`).join(' / ');
      li.append(note);$('event-log').append(li);
    });
  }
  function lineTrace(parts, colour, past, vehicleId) {
    const x=[],y=[];parts.forEach(([a,b])=>{x.push(a[0],b[0],null);y.push(a[1],b[1],null);});
    return {x,y,type:'scatter',mode:'lines',line:{color:colour,width:past?1.5:2.3,dash:past?'dot':'solid'},opacity:past?.28:.82,name:`Vehicle ${vehicleId}`,hoverinfo:'name',showlegend:false};
  }
  function drawMap() {
    const render=++renderNumber;
    renderQueue=renderQueue.catch(()=>{}).then(async()=>{
      if(render!==renderNumber || !frame) return;
      const data=frame.strategies[selected()], traces=[];
      data.routes.forEach(route=>{const colour=palette[route.vehicle_id%palette.length];if(route.past.length)traces.push(lineTrace(route.past,colour,true,route.vehicle_id));if(route.future.length)traces.push(lineTrace(route.future,colour,false,route.vehicle_id));});
      Object.entries(statusColours).forEach(([status,colour])=>{const customers=data.customers.filter(c=>c.status===status);if(!customers.length)return;
        traces.push({x:customers.map(c=>c.x),y:customers.map(c=>c.y),type:'scatter',mode:'markers+text',text:customers.map(c=>String(c.id)),textposition:'top center',textfont:{size:10,color:'#c5d4e1'},marker:{size:status==='servicing'?12:9,color:colour,symbol:status==='unassigned'?'x':'circle',line:{width:1,color:'#0b1420'}},customdata:customers.map(c=>[c.id,c.status,c.demand,c.ready,c.due,c.vehicle_id??'unassigned']),hovertemplate:'Order %{customdata[0]} · %{customdata[1]}<br>Demand %{customdata[2]} · window %{customdata[3]}–%{customdata[4]}<br>Vehicle %{customdata[5]}<extra></extra>',showlegend:false});});
      const moving=data.vehicles.filter(v=>v.status!=='idle'||v.x!==data.depot.x||v.y!==data.depot.y);
      traces.push({x:moving.map(v=>v.x),y:moving.map(v=>v.y),mode:'markers',type:'scatter',marker:{size:15,color:moving.map(v=>v.status==='broken'?'#ff7f88':palette[v.id%palette.length]),symbol:moving.map(v=>v.status==='broken'?'x':'triangle-up'),line:{width:1.5,color:'#ecf6ff'}},customdata:moving.map(v=>[v.id,v.status,v.used_capacity,v.capacity]),hovertemplate:'Vehicle %{customdata[0]} · %{customdata[1]}<br>Used capacity %{customdata[2]}/%{customdata[3]}<extra></extra>',showlegend:false});
      traces.push({x:[data.depot.x],y:[data.depot.y],mode:'markers+text',text:['DEPOT'],textposition:'bottom center',textfont:{size:10,color:'#fff'},marker:{size:14,symbol:'square',color:'#fff',line:{width:2,color:'#0b1420'}},hovertemplate:`Depot<br>Closes at ${frame.max_time}<br>${data.vehicles.filter(v=>v.status==='idle').length} idle vehicles<extra></extra>`,showlegend:false});
      if(preview)traces.push({x:[preview.x],y:[preview.y],mode:'markers',marker:{symbol:'cross',size:18,color:'#fff'},hovertemplate:'New delivery location<extra></extra>',showlegend:false});
      const all=[data.depot,...data.customers], xs=all.map(c=>c.x),ys=all.map(c=>c.y);
      const layout={paper_bgcolor:'#121f2e',plot_bgcolor:'#101b29',font:{color:'#95a9bc',family:'Segoe UI, Arial'},margin:{l:44,r:25,t:20,b:38},hovermode:'closest',dragmode:placing?false:'pan',uirevision:frame.session_id,xaxis:{title:{text:'X',font:{size:10}},range:[Math.min(...xs)-8,Math.max(...xs)+8],gridcolor:'#223346',zeroline:false},yaxis:{title:{text:'Y',font:{size:10}},range:[Math.min(...ys)-8,Math.max(...ys)+8],gridcolor:'#223346',zeroline:false,scaleanchor:'x',scaleratio:1},showlegend:false};
      await Plotly.react('map',traces,layout,{responsive:true,displaylogo:false,scrollZoom:true,modeBarButtonsToRemove:['select2d','lasso2d','toImage']});
      plotReady=true;
      $('map-subtitle').textContent=`${data.vehicles.filter(v=>v.status!=='idle'&&v.status!=='broken').length} active · ${data.vehicles.filter(v=>v.status==='broken').length} broken · ${frame.kpis[selected()].served_orders} delivered`;
    });
    return renderQueue;
  }
  async function loadDataset() {
    const reset=Boolean(frame?.event_count);
    if(reset && !confirm('Loading a dataset will reset all events and strategy histories. Continue?'))return;
    setBusy(true,'Solving initial routes…');message('Loading 25 customers and planning routes…');
    try {const data=await api('/load',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset:$('dataset').value,reset})});preview=null;await accept(data);$('order-ready').value=0;$('order-due').value=data.max_time;message(`${data.dataset} loaded. Press Play, or move the slider to t=90 and break an active vehicle.`);}
    catch(error){message(error.message,true);}finally{setBusy(false);}
  }
  async function sendEvent(payload) {
    if(!canEvent())return;
    const saved=frame;setBusy(true);message('Applying the event to all three strategies…');
    try {const data=await api('/event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,time:saved.time,session_id:saved.session_id,revision:saved.revision})});preview=null;await accept(data);$('order-ready').value=data.time;
      const failed=Object.entries(data.latest_event.strategies).filter(([,v])=>v.status==='SOLVE_FAILED');
      message(failed.length?`Event applied, but ${failed.map(([n])=>names[n]).join(', ')} could not re-plan. Previous healthy routes retained; inspect unassigned orders.`:'Event applied. Completed deliveries are locked; routes and strategy comparisons are updated.',Boolean(failed.length));
    }catch(error){message(error.message,true);if(error.status===409){setBusy(false);await fetchFrame(targetTime);}}
    finally{setBusy(false);$('placement-hint').textContent='Click to place, or enter x/y coordinates.';}
  }
  $('load').addEventListener('click',loadDataset);
  $('vehicle-select').addEventListener('change',vehicleDetail);
  $('strategy').addEventListener('change',()=>{updateVehicles();updateMetrics();drawMap();});
  $('time-slider').addEventListener('input',()=>{pause();cancelRead();setTime($('time-slider').value);debounce=setTimeout(()=>fetchFrame(targetTime),65);});
  $('jump-live').addEventListener('click',()=>{pause();setTime(frame.committed_time);fetchFrame(targetTime);});
  $('play').addEventListener('click',()=>{
    if(playing){pause();return;} if(!frame)return;
    playing=true;$('play').textContent='Ⅱ Pause';$('play').setAttribute('aria-label','Pause simulation');
    if(targetTime>=frame.max_time)setTime(0);
    async function tick(){if(!playing)return;const next=Math.min(frame.max_time,targetTime+Number($('speed').value)*.25);setTime(next);const ok=await fetchFrame(next);if(!playing)return;if(!ok||next>=frame.max_time){pause();return;}timer=setTimeout(tick,250);}tick();
  });
  $('break').addEventListener('click',()=>sendEvent({event_type:'breakdown',vehicle_id:Number($('vehicle-select').value)}));
  $('place').addEventListener('click',()=>{pause();placing=!placing;$('place').classList.toggle('active',placing);$('placement-hint').textContent=placing?'Click anywhere inside the plot to select the new delivery location.':'Click to place, or enter x/y coordinates.';drawMap();});
  $('map').addEventListener('click',event=>{
    if(!placing||!plotReady||!canEvent())return;
    const graph=$('map'), bounds=graph.getBoundingClientRect(), axes=graph._fullLayout;
    const px=event.clientX-bounds.left-axes.xaxis._offset, py=event.clientY-bounds.top-axes.yaxis._offset;
    if(px<0||py<0||px>axes.xaxis._length||py>axes.yaxis._length)return;
    const x=Number(axes.xaxis.p2d(px).toFixed(1)),y=Number(axes.yaxis.p2d(py).toFixed(1));
    $('order-x').value=x;$('order-y').value=y;$('order-ready').value=frame.time;
    preview={x,y};placing=false;$('place').classList.remove('active');$('placement-hint').textContent=`Selected (${x}, ${y}). Set demand and window, then add the order.`;drawMap();
  });
  $('order-form').addEventListener('submit',event=>{event.preventDefault();if(!canEvent()||!$('order-form').reportValidity())return;
    const payload={event_type:'new_order',x:Number($('order-x').value),y:Number($('order-y').value),demand:Number($('order-demand').value),ready:Number($('order-ready').value),due:Number($('order-due').value),service:Number($('order-service').value)};
    if(payload.ready>payload.due){message('The delivery window must open before it closes.',true);return;}sendEvent(payload);
  });
  updateMetrics();controls();
  if(!window.Plotly){message('Plotly could not load. Check static/vendor/plotly.min.js and refresh.',true);$('load').disabled=true;return;}
  // Reopening the page restores the shared session without silently resetting it.
  api('/state?t=0').then(async data=>{if(data.committed_time>0)data=await api(`/state?t=${data.committed_time}`);await accept(data);$('order-ready').value=data.time;$('order-due').value=data.max_time;message('Restored the current local session. Use Load & solve to start again.');}).catch(error=>{if(error.status!==409)message(error.message,true);});
})();

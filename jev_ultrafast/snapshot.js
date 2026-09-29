(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(document.getElementById(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  const weak = t => !t || /^(请输入|请选择|请填写|搜索|查询|search|select|enter|input|type|choose|pick|please\b.*|必填|请选择\.\.\.)[\s.…。:：]*$/i.test(t);
  const offscreen = o => {
    const r=o.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
    return r.width<=0 || r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=innerHeight;
  };
  const hiddenControls = e => {
    // Actions an overflow trigger hides: real controls in the nearest small container that the
    // clipped layout keeps out of the viewport. A person reaches them through the popup; synthetic
    // clicks cannot open every popup, so they are offered directly as well.
    let box=e.parentElement;
    for (let hops=0; box && hops<4; hops++, box=box.parentElement) {
      const controls=[...box.querySelectorAll('button,[role="button"],[role="menuitem"]')];
      if (controls.length < 2) continue;
      if (controls.length > 16) return [];  // a whole grid, not one action group
      const items=controls.filter(o=>o!==e && offscreen(o) && name(o) && !o.matches(':disabled'));
      if (items.length) return items;
    }
    return [];
  };
  const fillable = e => ['textbox','searchbox','spinbutton','combobox'].includes(role(e)) || e.isContentEditable;
  const nearby = e => {
    // Form frameworks without for/id links label a control with text that sits next to it.
    // Only fillable controls need this: their accessible name is often just the placeholder.
    const clean = n => (n.innerText || n.textContent || '').replace(/\s+/g, ' ').trim();
    const own = name(e);
    const usable = t => t && t.length <= 24 && !weak(t) && t !== own;
    let fallback = '', n = e;
    for (let hops = 0; n && hops < 5; hops++, n = n.parentElement) {
      const sibling = n.previousElementSibling;
      if (!sibling) continue;
      const text = clean(sibling);
      if (sibling.tagName === 'LABEL' && usable(text)) return text;
      if (usable(text) && !fallback) fallback = text;
    }
    return fallback;
  };
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],[tabindex="0"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
    }
    // A custom select renders as a focusable box rather than an input; a person opens it to see the
    // choices, so it is a trigger and not a text field.
    if (e.tabIndex >= 0 && /picker|select/i.test(String(e.className||''))) return 'combobox';
    return null;
  };
  // What makes a decision stale: a different document, a different viewport, or different field
  // values. Query-string churn and scroll offsets are not: apps rewrite the address as they render,
  // and the executor resolves geometry and hit-tests again before any input.
  cache.pageKey=()=>[performance.timeOrigin,location.origin+location.pathname,innerWidth,innerHeight,
    [...document.querySelectorAll('input,textarea,select')].filter(safe)
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly])];
  cache.guard=e=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||''];
  };
  const actions=[];
  for (const e of document.querySelectorAll(selector)) {
    if (!safe(e) || !visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=role(e);
    if (!rname || r.width<=0 || r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    const base={node:identity(e),role:rname,label:name(e)||rname,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    if (!name(e)) {
      const hidden=hiddenControls(e);
      if (hidden.length >= 2) {
        base.label='More: '+hidden.map(o=>name(o)).slice(0,6).join(', ');
        // Offer the hidden actions themselves: the popup may not open under synthetic input, and
        // the executor can still reach the observed node.
        for (const h of hidden.slice(0,8)) {
          const hr=h.getBoundingClientRect();
          actions.push({node:identity(h),role:'button',kind:'click',value:'',label:name(h),
            clipped:true,rect:{x:hr.x,y:hr.y,w:hr.width,h:hr.height}});
        }
      }
    }
    if (weak(base.label) && fillable(e)) { const near=nearby(e); if (near) base.nearby=near; }
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        actions.push({...base,kind:'select',value:o.value,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
    } else {
      // A picker's input is a trigger, not a text field: it owns a popup or cannot take focus.
      // Typing into one cannot set the value, so opening it is the only honest action.
      const formish=e.tagName==='INPUT' || e.tagName==='TEXTAREA' || e.isContentEditable || rname==='combobox';
      const readonly=e.readOnly || e.getAttribute('aria-readonly')==='true';
      const custom=rname==='combobox' && !['INPUT','TEXTAREA','SELECT'].includes(e.tagName);
      const popup=formish && (e.getAttribute('aria-haspopup')!==null || e.tabIndex<0 || custom);
      // A real text box stays typable even when it owns a suggestion popup: typing is how a search
      // box is used. Only a custom picker (a box that is not an input) is open-only.
      const editable=formish && !readonly &&
        (['textbox','searchbox','spinbutton'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      if (custom) {
        // Its own text is the current choice, not its name: the label beside it names the field.
        const near=nearby(e); if (near) base.label=near.replace(/[\s*＊·:：]+$/,'');
        base.value=value;
        // Chips fill the left of a multi-select; only the free space after them opens the popup.
        if (e.querySelector('[class*="tag"],[class*="chip"],[class*="token"]')) base.aim='free';
      }
      if (editable) {
        actions.push({...base,kind:'fill',value});
        actions.push({...base,kind:'click',value,label:'Open '+base.label});
      } else if (popup) {
        actions.push({...base,kind:'click',value,label:'Open '+base.label});
      } else {
        actions.push({...base,kind:'click',value});
      }
    }
  }
  // Closable chips (tags, filters, tokens) carry their remove affordance in a small icon whose
  // class names it; the label names what would be removed, so the choice is legible on its own.
  // Choices in a custom popup are plain rows: no role, no tabindex, nothing a role query finds.
  // While the popup is open they are the only way to pick a value, so they are offered as targets.
  // Only rows inside a floating container count: the same class names appear in fixed page chrome.
  const closable=/(^|[-_])(close|clear|remove|dismiss)([-_]|$)/i, taken=new Set(actions.map(a=>a.node));
  const optionish='[class*="option"],[class*="menuitem"],[class*="menu-item"]';
  const floating = e => {
    for (let p=e.parentElement, hops=0; p && hops<6; p=p.parentElement, hops++) {
      const position=getComputedStyle(p).position;
      if (position==='absolute' || position==='fixed') return true;
    }
    return false;
  };
  for (const e of document.querySelectorAll(optionish)) {
    if (actions.length > 220) break;
    if (!safe(e) || !visible(e) || e.matches(':disabled') || e.getAttribute('aria-disabled')==='true') continue;
    // A class that names the row an option is enough; a generic menu item needs a floating parent,
    // because the same class names appear in the fixed page chrome.
    const isOption=/option/i.test(String(e.className||''));
    if (e.querySelector(optionish) || (!isOption && !floating(e))) continue;
    const text=(e.innerText||'').replace(/\s+/g,' ').trim();
    if (!text || text.length > 40) continue;
    const node=identity(e);
    if (taken.has(node)) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
    if (r.width<8 || r.height<8 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
    actions.push({node,role:'option',kind:'click',value:'',label:text,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}});
  }
  for (const e of document.querySelectorAll('[class*="close"],[class*="clear"],[class*="remove"],[class*="dismiss"]')) {
    if (!closable.test(String(e.className||'')) || !safe(e) || !visible(e)) continue;
    const node=identity(e);
    if (taken.has(node)) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
    if (!r.width || !r.height || r.width>40 || r.height>40 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
    const text=(e.parentElement?.innerText||'').replace(/\s+/g,' ').trim();
    if (!text || text.length>40) continue;
    taken.add(node);
    actions.push({node,kind:'click',value:'',role:'button',label:'Remove "'+text+'"',
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}});
  }
  const words=[], walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  const range=document.createRange(); let node,length=0;
  while ((node=walker.nextNode()) && length<6000) {
    const value=node.textContent.trim(), parent=node.parentElement;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node); const r=range.getBoundingClientRect();
    if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
      words.push(value); length+=value.length;
    }
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[4]];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  // Long pages often scroll inside a panel rather than the window: the wheel goes to whatever sits
  // under the viewport centre, so that is what decides whether scrolling is still possible.
  const scrollable = () => {
    const scroller = e => {
      const cs=getComputedStyle(e);
      return /(auto|scroll)/.test(cs.overflowY) && e.scrollHeight > e.clientHeight + 4 ? e : null;
    };
    const atCentre = document.elementFromPoint(innerWidth/2, innerHeight/2);
    let panel=null;
    for (let p=atCentre, hops=0; p && hops<8; p=p.parentElement) if ((panel=scroller(p))) break;
    if (panel) return {down:panel.scrollTop+panel.clientHeight < panel.scrollHeight-2, up:panel.scrollTop>0};
    return {down:scrollY+innerHeight<height-2, up:scrollY>0};
  };
  const canScroll=scrollable();
  if (canScroll.down) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (canScroll.up) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions};
})()

import sys, json, subprocess, time
from playwright.sync_api import sync_playwright

URL="http://127.0.0.1:8765/app.html?ch=ch47&summary=1"
JS_PTR = """
([sel, type, ptype, x, y, pid, pressure]) => {
  const el=document.querySelector(sel); const r=el.getBoundingClientRect();
  const ev=new PointerEvent(type,{bubbles:true,cancelable:true,pointerType:ptype,pointerId:pid,isPrimary:pid===1,
     clientX:r.left+x, clientY:r.top+y, pressure:pressure==null?0.5:pressure, buttons:1});
  el.dispatchEvent(ev); return ev.defaultPrevented;
}"""
JS_TOUCH = """
([sel, n, ttype]) => {
  const el=document.querySelector(sel); const r=el.getBoundingClientRect(); const ts=[];
  let canTouch=true; try{ new Touch({identifier:1,target:el}); }catch(e){ canTouch=false; }
  if(!canTouch){ // WebKit: Touch 생성자 없음 → 핸들러 로직을 가짜 이벤트로 직접 검증
    const ev={touches:Array.from({length:n},()=>({touchType:ttype||"direct"})),p:false,preventDefault(){this.p=true}};
    inkTouchBlock(ev); return {prevented:ev.p, touchTypeSupported:true}; }
  for(let i=0;i<n;i++){ const init={identifier:i+10,target:el,clientX:r.left+50+i*40,clientY:r.top+60}; if(ttype) init.touchType=ttype; ts.push(new Touch(init)); }
  const ev=new TouchEvent('touchstart',{bubbles:true,cancelable:true,touches:ts,targetTouches:ts,changedTouches:ts});
  el.dispatchEvent(ev); return {prevented:ev.defaultPrevented, touchTypeSupported: ts[0].touchType!==undefined};
}"""
def run(browser_name):
    fails=[]; errors=[]
    def check(cond,msg):
        print(("  ✅ " if cond else "  ❌ ")+msg)
        if not cond: fails.append(msg)
    with sync_playwright() as p:
        b=getattr(p,browser_name).launch()
        ctx=b.new_context(viewport={"width":820,"height":1180},has_touch=True,device_scale_factor=2)
        ctx.add_init_script("try{localStorage.setItem('ox_gate_v2','2d4dd6abff89d0689f210ce69d566fb8d2d4a1c7941f9e17e357c67bcf9d208c')}catch(e){}")
        page=ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type=="error" else None)
        page.goto(URL); page.wait_for_selector("#introWrap.show", timeout=8000); page.wait_for_timeout(400)
        check(page.is_visible("#inkBtn"), "펜 버튼 노출")
        page.click("#inkBtn"); page.wait_for_timeout(150)
        check(page.is_visible("#inkBar"), "툴바 표시")
        check(page.evaluate("getComputedStyle(document.querySelector('.inkcv')).pointerEvents")=="auto", "캔버스 입력 활성")
        cv=".inkcv"; pen=lambda t,x,y,pid=1,pr=0.5: page.evaluate(JS_PTR,[cv,t,"pen",x,y,pid,pr])
        touch=lambda t,x,y,pid=7: page.evaluate(JS_PTR,[cv,t,"touch",x,y,pid,0.5])
        cnt=lambda: page.evaluate("INK.strokes.length")
        # 1) 형광펜 거의 수평 획 → 직선 스냅
        pen("pointerdown",40,40); [pen("pointermove",40+i*10,40+(i%2)*2) for i in range(1,20)]; pen("pointerup",230,40)
        check(cnt()==1, "형광펜 획 1개 기록")
        check(page.evaluate("INK.strokes[0].p.length")==2, "수평 형광펜 → 직선 스냅(점 2개)")
        # 2) 손바닥(touch) 단독 → 획 없음
        touch("pointerdown",100,120); touch("pointermove",120,140); touch("pointerup",130,150)
        check(cnt()==1, "손가락 터치는 획으로 기록 안 됨")
        # 3) 펜 획 도중 손바닥 + 두 번째 펜 포인터 → 무시
        page.click("button[data-tool='pr']")
        pen("pointerdown",60,200,1,0.3)
        touch("pointerdown",300,400,8); touch("pointermove",310,410,8)
        pen("pointerdown",400,500,2); pen("pointermove",420,520,2)
        for i in range(1,30): pen("pointermove",60+i*8,200+((i*37)%25),1,0.2+0.02*i)
        pen("pointerup",300,210,1); pen("pointerup",430,530,2); touch("pointerup",320,420,8)
        check(cnt()==2, "획 도중 손바닥·두번째 포인터 무시(총 2획)")
        check(page.evaluate("INK.strokes[1].p.length")>10 and page.evaluate("INK.strokes[1].t")=="pr", "펜 획은 곡선(점 다수)+빨강펜")
        check(page.evaluate("INK.strokes[1].p.some(q=>q[2]>0.6)"), "필압 저장됨")
        check(page.evaluate("INK.cur")is None and page.evaluate("INK.pid") is None, "획 종료 후 상태 초기화")
        # 4) 되돌리기/다시실행
        page.click("#inkUndo"); check(cnt()==1, "되돌리기")
        page.click("#inkRedo"); check(cnt()==2, "다시 실행")
        # 5) 지우개: 형광펜 직선 위 터치 → 삭제, redo 복구
        page.click("button[data-tool='er']"); pen("pointerdown",120,40); pen("pointerup",120,40)
        check(cnt()==1 and page.evaluate("INK.strokes[0].t")=="pr", "지우개로 형광펜 획 삭제")
        page.click("#inkRedo"); check(cnt()==2, "지운 획 다시 실행으로 복구")
        # 6) 터치 차단 (손바닥 방지)
        r=page.evaluate(JS_TOUCH,[cv,1,None]); check(r["prevented"]==False, "잠금 OFF: 한 손가락 스크롤 허용")
        page.click("#inkLock")
        r=page.evaluate(JS_TOUCH,[cv,1,None]); check(r["prevented"]==True, "잠금 ON: 한 손가락 차단")
        r=page.evaluate(JS_TOUCH,[cv,2,None]); check(r["prevented"]==False, "잠금 ON: 두 손가락 스크롤 허용")
        r=page.evaluate(JS_TOUCH,[cv,1,"stylus"])
        if r["touchTypeSupported"]: check(r["prevented"]==True, "펜슬 터치는 스크롤로 안 넘어감")
        else: print("  ⏭ 이 브라우저는 touchType 미지원 → 펜슬 터치 차단 테스트 생략")
        # 7) 저장·복원
        page.wait_for_timeout(200)
        key=page.evaluate("inkKey()")
        check(page.evaluate(f"JSON.parse(localStorage.getItem('{key}')).s.length")==2, "localStorage 저장")
        page.reload(); page.wait_for_selector("#introWrap.show", timeout=8000); page.wait_for_timeout(400)
        check(cnt()==2, "새로고침 후 필기 복원")
        # 8) 상세/쉬운말 전환 시 별도 저장
        if page.is_visible("#lvlBtn"):
            page.click("#lvlBtn"); page.wait_for_timeout(150); check(cnt()==0, "다른 요약 모드는 별도 필기(0개)")
            page.click("#lvlBtn"); page.wait_for_timeout(150); check(cnt()==2, "원래 모드로 돌아오면 필기 복원")
        # 9) 캔버스 크기 = 본문 크기
        sz=page.evaluate("(()=>{const b=document.getElementById('introBody'),c=document.querySelector('.inkcv');return [b.clientWidth,b.scrollHeight,c.offsetWidth,c.offsetHeight]})()")
        check(abs(sz[0]-sz[2])<=1 and abs(sz[1]-sz[3])<=1, f"캔버스가 본문 전체를 덮음 {sz}")
        # 10) 스크린샷
        page.screenshot(path=f"/tmp/ink_{browser_name}.png")
        b.close()
    errs=[e for e in errors if "favicon" not in e and "firebase" not in e.lower() and "net::" not in e]
    check(not errs, f"JS 오류 없음 {errs[:3]}")
    return fails
srv=subprocess.Popen([sys.executable,"-m","http.server","8765","--directory","public"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(1); total=[]
try:
    for bn in (sys.argv[1:] or ("chromium","webkit")):
        print(f"\n=== {bn} ==="); total+=[(bn,f) for f in run(bn)]
finally: srv.kill()
print("\nRESULT:", "ALL PASS" if not total else total)

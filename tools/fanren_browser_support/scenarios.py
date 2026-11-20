"""UI-only actions. Observations come from the real page; results come from HTTP receipts."""

import asyncio
import time

from .human_input import HumanInput
from .native_driver import NativeInputDriver


class NativeScenarios:
    def __init__(self,page):
        self.page=page;self.input=HumanInput(page)
        self.driver=NativeInputDriver(page,bridge='window.fanrenNative',canvas='canvas[data-fanren-native="scene"]')

    async def open_menu(self):
        menu=self.page.locator('#worldSystemMenu')
        if await menu.count() and not await menu.is_visible():
            await self.input.press('Escape')
            await menu.wait_for(state='visible',timeout=15000)
        return menu

    async def close_menu(self):
        menu=self.page.locator('#worldSystemMenu')
        if await menu.count() and await menu.is_visible():await self.input.press('Escape')

    async def low_graphics(self):
        state=await self.page.evaluate('window.fanrenNative.stats()')
        if state.get('performance',{}).get('quality')=='low':return
        await self.open_menu()
        entry=self.page.locator('#worldQuality')
        if not await entry.is_visible():return
        await self.input.click(entry)
        await self.input.click(self.page.locator('#worldQualityPanel [data-quality="low"]'))
        view=self.page.locator('#qualityViewDistance')
        if await view.is_visible():
            await self.input.hover(view);await view.focus();await self.input.press('Home')
        await self.input.click(self.page.locator('[data-quality-close]'))
        await self.close_menu()

    async def take_control(self):
        state=await self.page.evaluate('window.fanrenNative.stats()')
        if state['controlMode']=='companion':return True
        await self.open_menu()
        buttons=self.page.get_by_role('button',name='操控人物',exact=True).or_(
            self.page.get_by_role('button',name='操控南宫婉',exact=True)).or_(
            self.page.locator('#worldControl, #worldCompanionControl, #worldControlCompanion'))
        candidates=[]
        for index in range(await buttons.count()):
            item=buttons.nth(index)
            if await item.is_visible() and await item.is_enabled():candidates.append(item)
        if len(candidates)!=1:raise RuntimeError('native_control_button_unavailable')
        await self.input.click(candidates[0])
        await self.close_menu()
        await self.page.wait_for_function('() => window.fanrenNative.stats().controlMode==="companion"',timeout=10000,polling=1000)
        return True

    async def walk(self):
        state=await self.page.evaluate('window.fanrenNative.stats()')
        await self.take_control()
        x,y,z=state['actorPosition']
        # A short walk validates normal movement before any longer NPC route.
        result=await self.driver.move_to(x+1.5,z,timeout=12)
        if not result.get('reached'):return {'kind':'walk','result':result}
        await self.input.observe()
        return {'kind':'walk','result':result}

    async def approach(self,target):
        await self.take_control()
        x,y,z=target['point']
        state=await self.page.evaluate('window.fanrenNative.stats()')
        px,py,pz=state['actorPosition'];distance=((px-x)**2+(pz-z)**2)**.5
        radius=max(.4,min(target.get('radius',2.2)*.7,2))
        if distance>radius:
            tx=x+(px-x)/distance*radius*.8;tz=z+(pz-z)/distance*radius*.8
            moved=await self.driver.move_to(tx,tz,radius=.4,timeout=25)
            if not moved.get('reached'):return moved
        await self.input.observe()
        await self.input.press('e')
        return {'reached':True,'target':target['id']}

    async def npc(self):
        state=await self.page.evaluate('window.fanrenNative.stats()')
        px,py,pz=state['actorPosition']
        targets=[t for t in state.get('targets',[]) if t.get('kind') in {'town-npc','quest-object'}]
        targets.sort(key=lambda t:(t['point'][0]-px)**2+(t['point'][2]-pz)**2)
        if not targets:return {'kind':'npc','started':False,'reason':'no_visible_scene_targets'}
        target=targets[0];moved=await self.approach(target)
        if not moved.get('reached'):return {'kind':'npc','started':False,'navigation':moved}
        dialog=self.page.locator('.qingxi-quest-dialog:visible')
        await dialog.wait_for(state='visible',timeout=15000)
        available=[]
        for index in range(await dialog.get_by_role('button').count()):
            button=dialog.get_by_role('button').nth(index);text=(await button.inner_text()).strip()
            if text and text not in {'×','关闭'} and await button.is_enabled():available.append((text,button))
        options=dialog.locator('button.quest-option')
        if await options.count()==1:
            await self.input.click(options.first)
            available=[]
            for index in range(await dialog.get_by_role('button').count()):
                button=dialog.get_by_role('button').nth(index);text=(await button.inner_text()).strip()
                if text and await button.is_enabled():available.append((text,button))
        safe=[row for row in available if any(word in row[0] for word in ['接受任务','开始采集','全部拾取','完成任务'])]
        if len(safe)!=1:return {'kind':'npc','started':False,'reason':'choice_requires_review','buttons':[s for s,_ in available]}
        await self.input.click(safe[0][1])
        # A gathering animation must finish on the page's own clock; moving cancels it.
        gather=self.page.locator('.qingxi-gather:visible')
        if await gather.count():await gather.wait_for(state='hidden',timeout=90000)
        await asyncio.sleep(2)
        return {'kind':'npc','started':True,'target':target['id'],'action':safe[0][0]}

    async def fishing(self,timeout=180):
        panel=self.page.locator('#worldFishingPanel')
        if not await panel.is_visible():
            state=await self.page.evaluate('window.fanrenNative.stats()')
            targets=[t for t in state.get('targets',[]) if t.get('kind') in {'fishing-spot'}]
            if not targets:return {'kind':'fishing','started':False,'reason':'fishing_station_missing'}
            moved=await self.approach(targets[0])
            if not moved.get('reached'):return {'kind':'fishing','started':False,'navigation':moved}
        await panel.wait_for(state='visible',timeout=20000)
        phase=await panel.get_attribute('data-phase')
        if phase not in {'ready','result','miss','bite','waiting','fighting','caught'}:
            return {'kind':'fishing','started':False,'reason':'fishing_not_ready','phase':phase}
        cast=panel.locator('[data-fishing="cast"]')
        if phase in {'ready','result','miss'}:
            if not await cast.is_enabled():
                return {'kind':'fishing','started':False,'reason':'game_rejected_cast',
                        'message':await panel.locator('#worldFishingStatus').inner_text()}
            await self.input.click(cast)
        deadline=time.monotonic()+timeout;holding=False;last_phase=phase
        try:
            while time.monotonic()<deadline:
                await self.page.evaluate('window.fanrenNative.requireOwner()')
                phase=await panel.get_attribute('data-phase')
                if phase in {'result','miss'} and last_phase not in {'ready','result','miss'}:
                    return {'kind':'fishing','started':True,'ui_terminal':True,'phase':phase}
                if phase=='bite':
                    hook=panel.locator('[data-fishing="hook"]')
                    if await hook.is_enabled():await self.input.click(hook)
                elif phase=='caught':
                    land=panel.locator('[data-fishing="land"]')
                    if await land.is_enabled():await self.input.click(land)
                elif phase=='fighting':
                    cue=await panel.evaluate('''el=>({tension:Number(el.querySelector('#worldFishingTensionText').textContent),
                        low:parseFloat(el.querySelector('#worldFishingTensionZone').style.left),
                        width:parseFloat(el.querySelector('#worldFishingTensionZone').style.width)})''')
                    if not all(isinstance(cue.get(k),(int,float)) for k in ['tension','low','width']):
                        raise RuntimeError('visible_fishing_cues_missing')
                    low,high=cue['low']+cue['width']*.2,cue['low']+cue['width']*.8
                    reel=panel.locator('[data-fishing="reel"]')
                    if cue['tension']<low and not holding:
                        await self.input.hover(reel);await self.page.mouse.down();holding=True
                    elif cue['tension']>high and holding:
                        await self.page.mouse.up();holding=False
                elif phase=='error':
                    return {'kind':'fishing','started':True,'uncertain':True,
                            'message':await panel.locator('#worldFishingStatus').inner_text()}
                last_phase=phase
                await self.input.observe()
            return {'kind':'fishing','started':True,'uncertain':True,'reason':'deadline','phase':phase}
        finally:
            await self.page.mouse.up()

    async def combat(self,timeout=600):
        root=self.page.locator('.field-combat:visible')
        await root.wait_for(state='visible',timeout=30000)
        start=root.locator('[data-action="start"]:visible')
        if not await start.is_enabled():return {'kind':'combat','started':False,'reason':'game_rejected_start'}
        # Pick the lowest available tier through the actual buttons.
        down=root.locator('[data-action="tierDown"]:visible')
        for _ in range(30):
            if not await down.count() or not await down.is_enabled():break
            before=await self.page.evaluate('window.__fanrenCombat?.stats().trial?.tier?.chosen')
            if before==1:break
            await self.input.click(down)
            after=await self.page.evaluate('window.__fanrenCombat?.stats().trial?.tier?.chosen')
            if before==after:break
        state=await self.page.evaluate('window.__fanrenCombat?.stats()')
        if state.get('trial',{}).get('tier',{}).get('chosen')!=1:
            return {'kind':'combat','started':False,'reason':'lowest_tier_not_verified'}
        await self.input.click(start)
        canvas=self.page.locator('canvas[data-fanren-native="scene"]').first
        await canvas.focus()
        deadline=time.monotonic()+timeout;last_attack=0
        try:
            while time.monotonic()<deadline:
                await self.page.evaluate('window.fanrenNative.requireOwner()')
                state=await self.page.evaluate('window.__fanrenCombat.stats()')
                if state.get('state') in {'victory','defeat','ended','finished'}:
                    return {'kind':'combat','started':True,'ui_terminal':True,'state':state['state']}
                if state.get('state')=='fighting':
                    # Use ordinary hotkeys; the game's cooldown, targeting and costs decide acceptance.
                    if not state.get('target'):await self.input.press('Tab')
                    if time.monotonic()-last_attack>.65:
                        await self.input.press('j');last_attack=time.monotonic()
                    notice=await root.locator('[data-notice]').inner_text()
                    if any(word in notice for word in ['蓄力','重击','来袭']):
                        await self.input.press('q')
                await self.input.observe()
            return {'kind':'combat','started':True,'uncertain':True,'reason':'deadline'}
        finally:
            await self.input.release()

    async def close(self):
        await self.driver.stop();await self.input.release()

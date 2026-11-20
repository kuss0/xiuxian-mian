"""Bounded keyboard control using browser-native input and observed runtime state."""

import asyncio
import random
import time


class NativeInputDriver:
    def __init__(self, page, bridge='window.fanrenLab', canvas='#world'):
        self.page = page
        self.bridge = bridge
        self.canvas = canvas
        self.held = set()
        self.cancelled = asyncio.Event()
        self.active = False
        self.rng = random.SystemRandom()

    async def release(self):
        for key in list(self.held):
            await self.page.keyboard.up(key)
            self.held.discard(key)

    async def stop(self):
        self.cancelled.set()
        await self.release()

    async def move_to(self, x, z, *, radius=.35, timeout=15):
        if self.active:
            raise RuntimeError('movement_busy')
        if not .2 <= radius <= 5 or not 0 < timeout <= 30:
            raise ValueError('invalid_movement_bounds')
        self.active = True
        self.cancelled.clear()
        started = last_progress = time.monotonic()
        best, samples = float('inf'), 0
        try:
            await self.page.locator(self.canvas).focus()
            initial = await self.page.evaluate(f'{self.bridge}.stats()')
            if initial['gait'] == 'run':
                await self.page.keyboard.press('r')
            while not self.cancelled.is_set():
                plan = await self.page.evaluate(f'([x,z,r])=>{self.bridge}.planStep(x,z,r)', [x,z,radius])
                samples += 1
                now = time.monotonic()
                if not plan['ready']:
                    return {'reached': False, 'reason': plan['reason']}
                evidence = {'position': plan['position'], 'distance': plan['distance'], 'samples': samples}
                if plan['reached']:
                    return {'reached': True, **evidence}
                if now-started >= timeout:
                    return {'reached': False, 'reason': 'deadline', **evidence}
                if plan['distance'] < best-.08:
                    best, last_progress = plan['distance'], now
                if now-last_progress >= 2.8:
                    return {'reached': False, 'reason': 'blocked', **evidence}
                wanted = set(plan['keys'])
                for key in self.held-wanted:
                    await self.page.keyboard.up(key)
                for key in wanted-self.held:
                    await self.page.keyboard.down(key)
                self.held = wanted
                try:
                    await asyncio.wait_for(self.cancelled.wait(), timeout=self.rng.uniform(.18,.32))
                except TimeoutError:
                    pass
            return {'reached': False, 'reason': 'cancelled'}
        finally:
            await self.release()
            self.active = False

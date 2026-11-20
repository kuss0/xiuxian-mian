"""Normal browser mouse/keyboard gestures with bounded observation and reaction delays."""

import asyncio
import math
import random


class HumanInput:
    def __init__(self,page,rng=None):
        self.page=page;self.rng=rng or random.SystemRandom();self.position=(80,80);self.held=set()

    async def observe(self):
        await asyncio.sleep(self.rng.uniform(.18,.45))

    async def move_mouse(self,x,y):
        sx,sy=self.position;distance=math.hypot(x-sx,y-sy)
        steps=max(5,min(18,int(distance/45)+5))
        duration=self.rng.uniform(.16,.38)
        bend=self.rng.uniform(-18,18)
        for i in range(1,steps+1):
            t=i/steps;s=t*t*(3-2*t)
            await self.page.mouse.move(sx+(x-sx)*s+math.sin(t*math.pi)*bend,sy+(y-sy)*s)
            await asyncio.sleep(duration/steps)
        self.position=(x,y)

    async def hover(self,locator):
        await locator.wait_for(state='visible',timeout=15000)
        box=await locator.bounding_box()
        if not box:raise RuntimeError('target_not_visible')
        x=box['x']+box['width']*.5+self.rng.uniform(-min(4,box['width']*.1),min(4,box['width']*.1))
        y=box['y']+box['height']*.5+self.rng.uniform(-min(3,box['height']*.1),min(3,box['height']*.1))
        await self.move_mouse(x,y)

    async def click(self,locator):
        await self.hover(locator)
        if not await locator.is_enabled():raise RuntimeError('target_disabled')
        await self.observe()
        await self.page.mouse.down()
        try:await asyncio.sleep(self.rng.uniform(.07,.14))
        finally:await self.page.mouse.up()

    async def press(self,key):
        await self.page.keyboard.down(key);self.held.add(key)
        try:await asyncio.sleep(self.rng.uniform(.08,.18))
        finally:await self.page.keyboard.up(key);self.held.discard(key)

    async def release(self):
        await self.page.mouse.up()
        for key in list(self.held):
            await self.page.keyboard.up(key);self.held.discard(key)

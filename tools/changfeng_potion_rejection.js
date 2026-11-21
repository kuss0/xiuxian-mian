/* Keep native potion refusals from skipping the rest of the strategy's tick. */
(() => {
  const bot = window.__lw?.bot;
  if (typeof bot?.tick !== 'function' || typeof bot.tryPotion !== 'undefined')
    throw Error('trial_potion_rejection_bot_changed');
  const retryAt = Object.create(null);
  bot.tryPotion = function (combat, id, wallTime) {
    if (wallTime < (retryAt[id] ?? -Infinity)) return false;
    retryAt[id] = wallTime + 500;
    if (combat.command(id) !== true) return false;
    retryAt[id] = wallTime + 2000;
    return true;
  };
})();

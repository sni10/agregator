// Shared by the mockup pages: categories, dates, header.
window.CATEGORIES = {
  ai: { label: 'AI', badge: 'bg-violet-100 text-violet-800 dark:bg-violet-500/15 dark:text-violet-300',
        cover: 'from-violet-500 via-fuchsia-500 to-pink-500', glyph: '◆' },
  it: { label: 'IT', badge: 'bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-300',
        cover: 'from-sky-500 via-cyan-500 to-teal-400', glyph: '▲' },
  crypto: { label: 'Crypto', badge: 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300',
            cover: 'from-amber-500 via-orange-500 to-rose-500', glyph: '●' },
};

window.category = (card) => CATEGORIES[card.category] || CATEGORIES.it;

window.ago = (iso) => {
  if (!iso) return '';
  const date = new Date(iso);
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ', ' +
         date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
};

window.shuffle = (items) => {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
};

window.escapeHtml = (text) => String(text).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

window.header = (active) => `
  <header class="sticky top-0 z-10 border-b border-zinc-200 bg-white/80 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/80">
    <div class="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
      <a href="index.html" class="text-lg font-black tracking-tight">agregator<span class="text-violet-500">.</span></a>
      <nav class="flex gap-1 text-sm">
        ${[['all', 'All'], ['ai', 'AI'], ['it', 'IT'], ['crypto', 'Crypto']].map(([key, label]) => `
          <a href="index.html${key === 'all' ? '' : '#' + key}" data-cat="${key}"
             class="rounded-full px-3 py-1 font-medium ${key === active
               ? 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900'
               : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800'}">${label}</a>`).join('')}
      </nav>
      <span class="ml-auto hidden text-xs text-zinc-500 sm:block">IT · AI · Crypto news, facts checked against sources</span>
    </div>
  </header>`;

window.cover = (card, size) => {
  const cat = category(card);
  return `<div class="relative overflow-hidden bg-gradient-to-br ${cat.cover} ${size}">
    <span class="absolute -right-4 -bottom-10 select-none text-[9rem] leading-none text-white/20">${cat.glyph}</span>
    <span class="absolute left-3 top-3 rounded-full bg-black/25 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-white">${cat.label}</span>
  </div>`;
};

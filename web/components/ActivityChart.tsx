/** Last ~2 minutes: how "hot" the moment is (line) and chat messages (bars). */
export function ActivityChart({ interest, chat }: { interest: number[]; chat: number[] }) {
  const W = 300;
  const H = 70;
  const n = Math.max(interest.length, 2);
  const x = (i: number) => (i / (n - 1)) * W;
  const line = interest.map((v, i) => `${x(i).toFixed(1)},${(H - v * (H - 6) - 3).toFixed(1)}`).join(" ");
  const maxChat = Math.max(4, ...chat);
  return (
    <figure className="space-y-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-20 w-full" role="img" aria-label="Actividad de los últimos 2 minutos">
        <defs>
          <linearGradient id="hot" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#ec4899" stopOpacity="0.55" />
            <stop offset="1" stopColor="#a855f7" stopOpacity="0" />
          </linearGradient>
        </defs>
        {chat.map((c, i) => (
          <rect key={i} x={x(i) - 0.8} width="1.6" y={H - (c / maxChat) * (H * 0.6)} height={(c / maxChat) * (H * 0.6)}
                fill="#67e8f9" opacity="0.45" />
        ))}
        {interest.length > 1 && (
          <>
            <polygon points={`0,${H} ${line} ${W},${H}`} fill="url(#hot)" />
            <polyline points={line} fill="none" stroke="#f472b6" strokeWidth="2" strokeLinejoin="round" />
          </>
        )}
      </svg>
      <figcaption className="flex justify-between text-[11px] text-white/40">
        <span><span className="text-pink-400">━</span> momento</span>
        <span><span className="text-cyan-300">▮</span> chat</span>
        <span>últimos 2 min</span>
      </figcaption>
    </figure>
  );
}

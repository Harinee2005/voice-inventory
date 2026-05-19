export default function WaveformAnimation({ isActive, barCount = 20 }) {
  return (
    <div className="flex items-end justify-center gap-[3px] h-12">
      {Array.from({ length: barCount }).map((_, i) => (
        <div
          key={i}
          className="wave-bar rounded-sm"
          style={{
            height: isActive ? `${Math.random() * 70 + 30}%` : '20%',
            animationDuration: isActive ? `${0.6 + Math.random() * 0.6}s` : '0s',
            animationDelay: `${(i / barCount) * 0.5}s`,
            background: isActive
              ? `hsl(${210 + i * 3}, 80%, 65%)`
              : '#1e2d4a',
            transition: 'background 0.3s ease',
            minHeight: '6px',
            animation: isActive ? undefined : 'none',
            transform: isActive ? undefined : 'scaleY(0.3)',
          }}
        />
      ))}
    </div>
  )
}

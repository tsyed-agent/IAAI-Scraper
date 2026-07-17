import { useEffect, useRef, useState } from 'react'

export function LazyThumb({
  href,
  alt,
  className = '',
}: {
  href?: string | null
  alt: string
  className?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [src, setSrc] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setLoaded(false)
    setFailed(false)
    setSrc(null)
    if (!href) return

    const el = ref.current
    if (!el) return

    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSrc(href)
          io.disconnect()
        }
      },
      { rootMargin: '240px 0px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [href])

  return (
    <div ref={ref} className={`thumb-wrap ${className}`.trim()}>
      {!loaded && !failed && <div className="thumb-shimmer" aria-hidden />}
      {(!href || failed) && <div className="thumb-fallback">No photo</div>}
      {src && !failed && (
        <img
          src={src}
          alt={alt}
          loading="lazy"
          decoding="async"
          className={loaded ? 'loaded' : ''}
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
        />
      )}
    </div>
  )
}

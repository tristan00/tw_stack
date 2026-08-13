import { Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { Dot, IdentLabel } from '@/components/primitives'
import { useApi, type RunPage } from '@/lib/api'
import { ago } from '@/lib/format'
import { cn } from '@/lib/utils'


const NAV = [
  { to: '/run', label: 'run', asks: 'is it healthy right now' },
  { to: '/campaigns', label: 'campaigns', asks: 'how are campaigns going' },
  { to: '/decisions', label: 'decisions', asks: 'what did it choose and why' },
  { to: '/models', label: 'models', asks: 'are the models learning' },
  { to: '/infra', label: 'infra', asks: 'services and controls' },
]

type Theme = 'system' | 'light' | 'dark'

function useTheme() {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem('theme') as Theme) || 'system',
  )
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])
  return { theme, setTheme }
}


function StatusLine() {
  const { data } = useApi<RunPage>('/api/run')
  const cur = data?.current
  const services = data?.services ?? []
  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-4 gap-y-1">
      <div className="min-w-0">
        {cur?.campaign ? (
          <IdentLabel ident={cur.campaign} className="text-lg font-semibold tracking-tight" />
        ) : (
          <span className="text-dim text-lg">no campaign running</span>
        )}
      </div>
      {cur?.turn !== null && cur?.turn !== undefined && (
        <span className="text-dim text-2xs uppercase tracking-wide">
          turn <b className="num text-fg text-sm">{cur.turn}</b>
        </span>
      )}
      {cur?.settlements !== null && cur?.settlements !== undefined && (
        <span className="text-dim text-2xs uppercase tracking-wide">
          setts <b className="num text-fg text-sm">{cur.settlements}</b>
        </span>
      )}
      {cur?.power_rank !== null && cur?.power_rank !== undefined && (
        <span className="text-dim text-2xs uppercase tracking-wide">
          rank <b className="num text-fg text-sm">{cur.power_rank}</b>
        </span>
      )}
      <span className="flex items-center gap-2.5">
        {services
          .filter((s) => s.name !== 'dashboard')
          .map((s) => (
            <span key={s.name} className="text-dim flex items-center gap-1 text-2xs">
              <Dot state={s.up ? 'ok' : 'bad'} />
              {s.name}
            </span>
          ))}
      </span>
      {cur?.age_seconds !== null && cur?.age_seconds !== undefined && (
        <span className="text-dim ml-auto text-2xs">state {ago(cur.age_seconds)}</span>
      )}
    </div>
  )
}

export function Layout() {
  const { theme, setTheme } = useTheme()
  return (
    <div className="mx-auto flex min-h-full max-w-[1800px] flex-col px-4 py-3">
      <header className="border-line mb-4 border-b pb-3">
        <div className="mb-2 flex items-center justify-between gap-4">
          <StatusLine />
          <button
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            className="border-line text-dim hover:text-fg shrink-0 rounded-md border p-1.5"
            title={`theme: ${theme} — click to switch`}
            aria-label="switch theme"
          >
            {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </button>
        </div>
        <nav className="flex flex-wrap gap-1">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              title={item.asks}
              className={({ isActive }) =>
                cn(
                  'rounded-md px-3 py-1.5 text-sm',
                  isActive
                    ? 'bg-raised text-fg font-semibold'
                    : 'text-dim hover:text-fg hover:bg-raised/60',
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="min-w-0 flex-1 pb-10">
        <Outlet />
      </main>
    </div>
  )
}

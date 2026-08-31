import { FlaskConical, Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { EntityLink, Dot } from '@/components/primitives'
import { QuickJump } from '@/components/QuickJump'
import { mapShort } from '@/components/startcharts'
import { useApi, type RunPage } from '@/lib/api'
import { ago } from '@/lib/format'
import { cn } from '@/lib/utils'


const CATALOG_PATHS = ['/items', '/buildings', '/research', '/skills']

const GAME_NAV = [
  { to: '/campaigns', label: 'campaigns', asks: 'how are campaigns going' },
  { to: '/lookup', label: 'lookup', asks: 'which campaigns passed through situations like this' },
  { to: '/items', label: 'catalog', asks: 'items, buildings, research and skills across the corpus' },
  { to: '/status', label: 'status', asks: 'are the services alive and the streams writing' },
]

const STACK_NAV = [
  { to: '/run', label: 'run', asks: 'is the automated run healthy right now' },
  { to: '/decisions', label: 'decisions', asks: 'what the advisor chose and why' },
  { to: '/positions', label: 'positions', asks: 'what the advisor took in situations like this' },
  { to: '/log', label: 'log', asks: 'what exactly happened, second by second' },
  { to: '/selector', label: 'selector', asks: 'why the selector played this start' },
  { to: '/models', label: 'models', asks: 'are the models learning' },
  { to: '/infra', label: 'infra', asks: 'stack controls and launches' },
]

const devListeners = new Set<() => void>()

function devModeOn(): boolean {
  try {
    return localStorage.getItem('devmode') === '1'
  } catch {
    return false
  }
}

function setDevMode(on: boolean) {
  try {
    localStorage.setItem('devmode', on ? '1' : '0')
  } catch {
    return
  }
  devListeners.forEach((fn) => fn())
}

function useServerMode(): 'full' | 'dashboard' {
  const { data } = useApi<{ mode?: string }>('/api/health', [], { live: false })
  return data?.mode === 'dashboard' ? 'dashboard' : 'full'
}

function useDevMode(): boolean {
  const [dev, setDev] = useState(devModeOn)
  useEffect(() => {
    const fn = () => setDev(devModeOn())
    devListeners.add(fn)
    return () => {
      devListeners.delete(fn)
    }
  }, [])
  return dev
}

export function useUiMode(): 'full' | 'dashboard' {
  const server = useServerMode()
  const dev = useDevMode()
  return server === 'dashboard' ? 'dashboard' : dev ? 'full' : 'dashboard'
}

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
          <span className="flex items-baseline gap-2">
            <EntityLink
              to={`/campaigns/${encodeURIComponent(cur.campaign.raw)}`}
              title={`open this campaign\n${cur.campaign.raw}`}
              className="text-lg font-semibold tracking-tight"
            >
              {cur.leader ?? cur.campaign.label}
            </EntityLink>
            {cur.campaign.culture && (
              <span className="text-dim text-2xs">{cur.campaign.culture}</span>
            )}
            {cur.faction_key && (
              <EntityLink
                to={`/starts/${encodeURIComponent(cur.campaign_map?.raw ?? '')}/${encodeURIComponent(cur.faction_key)}`}
                title="open this start"
                className="text-dim text-2xs"
              >
                {mapShort(cur.campaign_map?.raw, cur.campaign_map?.label)}
              </EntityLink>
            )}
          </span>
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
      {cur?.stored_campaigns !== null && cur?.stored_campaigns !== undefined && (
        <span className="text-dim text-2xs uppercase tracking-wide">
          campaigns <b className="num text-fg text-sm">{cur.stored_campaigns}</b>
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
  const server = useServerMode()
  const dev = useDevMode()
  const mode = server === 'dashboard' ? 'dashboard' : dev ? 'full' : 'dashboard'
  const { pathname } = useLocation()
  const nav = mode === 'dashboard' ? GAME_NAV : [...GAME_NAV, ...STACK_NAV]
  return (
    <div className="mx-auto flex min-h-full max-w-[1800px] flex-col px-4 py-3">
      <header className="border-line mb-4 border-b pb-3">
        <div className="mb-2 flex items-center justify-between gap-4">
          <StatusLine />
          <span className="flex shrink-0 items-center gap-1.5">
            {server === 'full' && (
              <button
                onClick={() => setDevMode(!dev)}
                className={cn(
                  'border-line rounded-md border p-1.5',
                  dev ? 'text-accent' : 'text-dim hover:text-fg',
                )}
                title={
                  dev
                    ? 'dev dashboard on — click for the default dashboard alone'
                    : 'default dashboard — click to also show the dev dashboard (run, decisions, positions, log, selector, models, infra)'
                }
                aria-label="toggle dev mode"
              >
                <FlaskConical className="size-4" />
              </button>
            )}
            <button
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              className="border-line text-dim hover:text-fg rounded-md border p-1.5"
              title={`theme: ${theme} — click to switch`}
              aria-label="switch theme"
            >
              {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
          </span>
        </div>
        <nav className="flex flex-wrap items-center gap-1">
          {nav.map((item) => (
            <span key={item.to} className="flex items-center">
              {item.to === '/run' && (
                <span className="flex items-center" aria-hidden>
                  <span className="bg-line mx-1.5 h-4 w-px" />
                  <span className="text-dim mr-1 text-2xs tracking-wide uppercase">dev</span>
                </span>
              )}
              <NavLink
                to={item.to}
                title={item.asks}
                className={({ isActive }) =>
                  cn(
                    'rounded-md px-3 py-1.5 text-sm',
                    (item.label === 'catalog'
                      ? CATALOG_PATHS.some((c) => pathname.startsWith(c))
                      : isActive)
                      ? 'bg-raised text-fg font-semibold'
                      : 'text-dim hover:text-fg hover:bg-raised/60',
                  )
                }
              >
                {item.label}
              </NavLink>
            </span>
          ))}
          <span className="text-dim num ml-auto text-2xs">ctrl+k jump</span>
        </nav>
      </header>
      <main className="min-w-0 flex-1 pb-10">
        <Outlet />
      </main>
      <QuickJump />
    </div>
  )
}

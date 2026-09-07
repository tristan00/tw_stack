import { Moon, Sun, Swords } from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  Link,
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useSearchParams,
} from 'react-router-dom'
import { QuickJump } from '@/components/QuickJump'
import { useApi } from '@/lib/api'
import {
  GameContext,
  SUBJECTS,
  mapName,
  type CampaignChoice,
  type GamePage,
} from '@/lib/game'
import { ago } from '@/lib/format'
import { cn } from '@/lib/utils'

const MODEL_PATHS = [
  '/run',
  '/experiments',
  '/models',
  '/decisions',
  '/database',
  '/infra',
  '/log',
  '/selector',
  '/positions',
]
const MODEL_NAV = [
  { to: '/run', label: 'Run' },
  { to: '/experiments', label: 'Experiments' },
  { to: '/decisions', label: 'Decisions' },
  { to: '/database', label: 'Database' },
]

export function useUiMode(): 'full' | 'dashboard' {
  const { pathname } = useLocation()
  return MODEL_PATHS.some((p) => pathname.startsWith(p)) ? 'full' : 'dashboard'
}

export function Layout() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { data: health } = useApi<{ mode: string }>('/api/health', [], {
    live: false,
  })
  const choices = useApi<CampaignChoice[]>('/api/game/campaigns')
  const pathKey = pathname.startsWith('/campaigns/')
    ? decodeURIComponent(pathname.split('/')[2])
    : ''
  const campaignKey =
    pathKey || params.get('campaign') || choices.data?.[0]?.key || ''
  const turn = params.get('turn')
  const game = useApi<GamePage>(
    campaignKey
      ? `/api/game/campaigns/${encodeURIComponent(campaignKey)}${turn ? `?turn=${encodeURIComponent(turn)}` : ''}`
      : null,
    [campaignKey, turn],
  )
  const model = useUiMode() === 'full'
  const area = model
    ? 'Model'
    : pathname.startsWith('/analytics') || pathname === '/lookup'
      ? 'Analytics'
      : pathname.startsWith('/explore') ||
          ['/items', '/research', '/skills', '/traits', '/buildings'].some(
            (p) => pathname.startsWith(p),
          )
        ? 'Explore'
        : ['/options', '/status'].includes(pathname)
          ? 'Options'
          : 'Campaign'
  const [theme, setTheme] = useState(
    () => localStorage.getItem('theme') || 'light',
  )
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])
  useEffect(() => {
    document.documentElement.setAttribute(
      'data-ui-mode',
      model ? 'full' : 'dashboard',
    )
  }, [model])
  const context = new URLSearchParams()
  if (campaignKey) context.set('campaign', campaignKey)
  if (turn) context.set('turn', turn)
  const to = (path: string) => `${path}?${context}`
  const selected =
    game.data?.meta.key === campaignKey &&
    (!turn || game.data.selected.turn === Number(turn))
      ? game.data
      : null
  const subject = params.get('subject') || 'home'
  const changeCampaign = (key: string) =>
    navigate(`/?campaign=${encodeURIComponent(key)}&subject=${subject}`)
  return (
    <GameContext.Provider
      value={{
        ...game,
        data: selected,
        campaignKey,
        error: game.error || choices.error,
        loading: game.loading || choices.loading,
      }}
    >
      <div className="app-shell">
        <header className="app-header">
          <div className="top-bar">
            <Link to={to('/')} className="brand" aria-label="Campaign home">
              <Swords size={19} />
            </Link>
            <nav aria-label="Main navigation" className="top-nav">
              {[
                ['Campaign', '/'],
                ['Analytics', '/analytics'],
                ['Explore', '/explore'],
                ['Options', '/options'],
              ].map(([label, path]) => (
                <Link
                  key={path}
                  to={to(path)}
                  className={cn('top-link', area === label && 'active')}
                >
                  {label}
                </Link>
              ))}
            </nav>
            <div className="campaign-switch">
              <select
                aria-label="Campaign"
                value={campaignKey}
                onChange={(e) => changeCampaign(e.target.value)}
              >
                {pathKey && !choices.data?.some((c) => c.key === pathKey) && (
                  <option value={pathKey}>
                    {selected?.meta.leader || pathKey}
                  </option>
                )}
                {(choices.data || []).map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.leader || c.faction} · {mapName(c.map)} ·{' '}
                    {new Date(c.ts * 1000).toLocaleDateString()} · t{c.turn} ·{' '}
                    {c.key.slice(-6)}
                  </option>
                ))}
              </select>
              {selected && (
                <span className="recorded">
                  <span className="text-ok">●</span> turn{' '}
                  {selected.selected.turn} · recorded{' '}
                  {ago(Math.max(0, Date.now() / 1000 - selected.selected.ts))}
                </span>
              )}
            </div>
            {health?.mode === 'full' && (
              <div className="mode-switch" aria-label="Dashboard mode">
                <Link to={to('/')} className={!model ? 'active' : ''}>
                  Game
                </Link>
                <Link to={to('/run')} className={model ? 'active' : ''}>
                  Model
                </Link>
              </div>
            )}
            <button
              className="theme-button"
              aria-label="Switch theme"
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            >
              {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            </button>
          </div>
          <nav className="subject-nav" aria-label={`${area} navigation`}>
            <span className="nav-caption">{area}</span>
            {model ? (
              MODEL_NAV.map((i) => (
                <Link
                  key={i.to}
                  to={to(i.to)}
                  className={cn('subject-link', pathname === i.to && 'active')}
                >
                  {i.label}
                </Link>
              ))
            ) : area === 'Campaign' ? (
              SUBJECTS.map((s) => (
                <Link
                  key={s}
                  to={`/?${context}&subject=${s.toLowerCase()}`}
                  className={cn(
                    'subject-link',
                    subject === s.toLowerCase() && 'active',
                  )}
                >
                  {s}
                </Link>
              ))
            ) : area === 'Analytics' ? (
              <>
                <Link className="subject-link" to={to('/analytics')}>
                  Exclusive choices
                </Link>
                <Link className="subject-link" to={to('/lookup')}>
                  Campaign lookup
                </Link>
              </>
            ) : area === 'Explore' ? (
              <>
                <Link className="subject-link" to={to('/explore')}>
                  Records
                </Link>
                {['items', 'research', 'skills', 'buildings', 'traits'].map(
                  (s) => (
                    <Link
                      className={cn(
                        'subject-link',
                        pathname === `/${s}` && 'active',
                      )}
                      key={s}
                      to={to(`/${s}`)}
                    >
                      {s}
                    </Link>
                  ),
                )}
              </>
            ) : (
              <>
                <Link className="subject-link" to={to('/options')}>
                  Score & preferences
                </Link>
                <Link className="subject-link" to={to('/status')}>
                  Services
                </Link>
              </>
            )}
          </nav>
        </header>
        <main className="app-main">
          {model && health?.mode === 'dashboard' ? (
            <Navigate to="/" replace />
          ) : (
            <Outlet />
          )}
        </main>
        <QuickJump />
      </div>
    </GameContext.Provider>
  )
}

import { lazy, Suspense, type ReactNode } from 'react'
import { createBrowserRouter, Navigate, RouterProvider, useRouteError } from 'react-router-dom'
import CookieNotice from './CookieNotice'
import Landing from './pages/Landing'
import Login from './pages/Login'
import Privacy from './pages/Privacy'
import Shell from './pages/Shell'
import { ErrorBox } from './ui'

const Overview = lazy(() => import('./pages/Overview'))
const Customers = lazy(() => import('./pages/Customers'))
const Customer = lazy(() => import('./pages/Customer'))
const Model = lazy(() => import('./pages/Model'))
const DataPage = lazy(() => import('./pages/Data'))
const Guide = lazy(() => import('./pages/Guide'))
const Retention = lazy(() => import('./pages/Retention'))
const Team = lazy(() => import('./pages/Team'))
const Ask = lazy(() => import('./pages/Ask'))

const page = (node: ReactNode) => <Suspense fallback={null}>{node}</Suspense>

/** Экран упал — сообщение на его месте, меню панели остаётся. */
function ScreenError() {
  const e = useRouteError()
  const msg = e instanceof Error ? e.message : String(e)
  return <ErrorBox message={`Экран не открылся (${msg}). Обновите страницу; если повторится — напишите через «Обратная связь».`}
    onRetry={() => window.location.reload()} />
}

const router = createBrowserRouter([
  { path: '/', element: <Landing /> },
  { path: '/login', element: <Login /> },
  { path: '/privacy', element: <Privacy /> },
  {
    path: '/app',
    element: <Shell />,
    children: [{ errorElement: <ScreenError />, children: [
      { index: true, element: page(<Overview />) },
      { path: 'customers', element: page(<Customers />) },
      { path: 'customers/:id', element: page(<Customer />) },
      { path: 'retention', element: page(<Retention />) },
      { path: 'model', element: page(<Model />) },
      { path: 'data', element: page(<DataPage />) },
      { path: 'team', element: page(<Team />) },
      { path: 'ask', element: page(<Ask />) },
      { path: 'guide', element: page(<Guide />) },
    ] }],
  },
  { path: '*', element: <Navigate to="/" replace /> },
])

export default function App() {
  return <><RouterProvider router={router} /><CookieNotice /></>
}

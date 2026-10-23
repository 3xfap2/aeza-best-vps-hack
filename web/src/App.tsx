import { lazy, Suspense, type ReactNode } from 'react'
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom'
import Landing from './pages/Landing'
import Login from './pages/Login'
import Shell from './pages/Shell'

const Overview = lazy(() => import('./pages/Overview'))
const Customers = lazy(() => import('./pages/Customers'))
const Customer = lazy(() => import('./pages/Customer'))
const Model = lazy(() => import('./pages/Model'))
const DataPage = lazy(() => import('./pages/Data'))

const page = (node: ReactNode) => <Suspense fallback={null}>{node}</Suspense>

const router = createBrowserRouter([
  { path: '/', element: <Landing /> },
  { path: '/login', element: <Login /> },
  {
    path: '/app',
    element: <Shell />,
    children: [
      { index: true, element: page(<Overview />) },
      { path: 'customers', element: page(<Customers />) },
      { path: 'customers/:id', element: page(<Customer />) },
      { path: 'model', element: page(<Model />) },
      { path: 'data', element: page(<DataPage />) },
    ],
  },
  { path: '*', element: <Navigate to="/" replace /> },
])

export default function App() {
  return <RouterProvider router={router} />
}

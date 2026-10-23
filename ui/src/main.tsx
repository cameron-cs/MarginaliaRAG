import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Outlet, RouterProvider } from 'react-router'

import { Nav, ToastProvider } from './components/ui'
import { AdminPage } from './pages/Admin'
import { AuthPage } from './pages/Auth'
import { BookPage } from './pages/Book'
import { ShelfPage } from './pages/Shelf'
import { Protected, SessionProvider } from './session'
import './styles.css'

function Layout() {
  return (
    <>
      <Nav />
      <Outlet />
    </>
  )
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: '/signin', element: <AuthPage mode="signin" /> },
      { path: '/signup', element: <AuthPage mode="signup" /> },
      { path: '/', element: <Protected><ShelfPage /></Protected> },
      { path: '/books/:id', element: <Protected><BookPage /></Protected> },
      { path: '/admin', element: <Protected admin><AdminPage /></Protected> },
      { path: '/admin/:tab', element: <Protected admin><AdminPage /></Protected> },
      { path: '*', element: <main className="page page-narrow"><div className="card empty"><h2>Page not found.</h2></div></main> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SessionProvider>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </SessionProvider>
  </StrictMode>,
)

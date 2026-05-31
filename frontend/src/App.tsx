import { NavLink, Route, Routes } from "react-router-dom";
import Library from "./pages/Library";
import WarmUp from "./pages/WarmUp";
import Watch from "./pages/Watch";
import Spar from "./pages/Spar";
import Extract from "./pages/Extract";

function Tab({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `rounded-lg px-3 py-1.5 text-sm font-medium transition ${
          isActive
            ? "bg-indigo-600 text-white"
            : "text-neutral-600 hover:bg-neutral-200 dark:text-neutral-300 dark:hover:bg-neutral-800"
        }`
      }
    >
      {label}
    </NavLink>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-neutral-50 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100">
      <header className="sticky top-0 z-10 border-b border-neutral-200 bg-white/80 backdrop-blur dark:border-neutral-800 dark:bg-neutral-900/80">
        <div className="mx-auto flex max-w-5xl items-center gap-2 px-4 py-3">
          <NavLink to="/" className="mr-2 text-lg font-bold">
            한국어 <span className="text-indigo-600">Immersion</span>
          </NavLink>
          <Tab to="/" label="Library" />
          <Tab to="/warmup" label="Warm-up" />
        </div>
      </header>
      <main className="mx-auto max-w-5xl p-4">
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/warmup" element={<WarmUp />} />
          <Route path="/extract/:sid" element={<Extract />} />
          <Route path="/watch/:sid" element={<Watch />} />
          <Route path="/spar/:sid" element={<Spar />} />
        </Routes>
      </main>
    </div>
  );
}

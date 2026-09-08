import { Link, NavLink, Route, Routes } from "react-router-dom";

import { LogScreen } from "./screens/LogScreen";
import { SpotScreen } from "./screens/SpotScreen";
import { WindowsScreen } from "./screens/WindowsScreen";

function NotFound() {
  return (
    <>
      <h1 className="spot-title">Nothing here</h1>
      <p className="sub">
        <Link to="/">Back to today</Link>
      </p>
    </>
  );
}

export function App() {
  return (
    <div className="app">
      <header>
        <Link className="wordmark" to="/">
          gogo
        </Link>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<WindowsScreen />} />
          <Route path="/spot/:spotId" element={<SpotScreen />} />
          <Route path="/log" element={<LogScreen />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <nav className="tabbar">
        <NavLink to="/" end>
          Today
        </NavLink>
        <NavLink to="/log">Log</NavLink>
      </nav>
    </div>
  );
}

import { useLayoutEffect } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";

import { lisbonHeaderDate } from "./format";
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
  const location = useLocation();
  const room = location.pathname.startsWith("/log") ? "notebook" : "chart";

  useLayoutEffect(() => {
    document.documentElement.dataset.room = room;
    const theme = document.querySelector('meta[name="theme-color"]');
    theme?.setAttribute("content", room === "notebook" ? "#141210" : "#071018");
  }, [room]);

  return (
    <div className={`app room-${room}`}>
      <header>
        <Link className="wordmark" to="/" aria-label="gogo">
          go<span>go</span>
        </Link>
        <span className="when">{room === "notebook" ? "after" : lisbonHeaderDate()}</span>
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

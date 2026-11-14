import { NavLink, Route, Routes } from "react-router-dom";
import KnowledgePage from "./pages/KnowledgePage";
import QAPage from "./pages/QAPage";

export default function App() {
  return (
    <div className="app">
      <nav className="nav">
        <span className="nav-brand">审计智能问答</span>
        <div className="nav-links">
          <NavLink to="/" end className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
            💬 问答
          </NavLink>
          <NavLink to="/knowledge" className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
            📚 知识库
          </NavLink>
        </div>
      </nav>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<QAPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
        </Routes>
      </main>
    </div>
  );
}

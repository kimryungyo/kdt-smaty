import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";

const root = document.getElementById("root");

if (!root) {
  throw new Error("React root 요소를 찾을 수 없습니다.");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

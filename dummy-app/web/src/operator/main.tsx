import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { AppProviders } from "../shared/AppProviders.tsx";
import "../shared/global.css";
import { operatorRoutes } from "./routes.tsx";

const router = createBrowserRouter(operatorRoutes, { basename: import.meta.env.BASE_URL.replace(/\/$/, "") });

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  </StrictMode>,
);

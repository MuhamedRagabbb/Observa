import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { AppProviders } from "../shared/AppProviders.tsx";
import { APP_PATHS, routerBasename } from "../shared/bundle-path.ts";
import "../shared/global.css";
import { operatorRoutes } from "./routes.tsx";

const router = createBrowserRouter(operatorRoutes, { basename: routerBasename(APP_PATHS.control) });

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  </StrictMode>,
);

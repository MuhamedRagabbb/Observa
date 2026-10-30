import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { AppProviders } from "../shared/AppProviders.tsx";
import { APP_PATHS, routerBasename } from "../shared/bundle-path.ts";
import "../shared/global.css";
import { storeRoutes } from "./routes.tsx";

// The store's pages are at / on the Gateway (P4-H); its files stay under /ui/next/assets/.
const router = createBrowserRouter(storeRoutes, { basename: routerBasename(APP_PATHS.store) });

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  </StrictMode>,
);

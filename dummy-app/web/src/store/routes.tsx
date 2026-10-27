import type { RouteObject } from "react-router";
import { StoreLayout } from "./StoreLayout.tsx";

export const storeRoutes: RouteObject[] = [
  {
    path: "/",
    element: <StoreLayout />,
  },
];

import type { RouteObject } from "react-router";
import { HomePage } from "./home/HomePage.tsx";
import { StoreLayout } from "./StoreLayout.tsx";

export const storeRoutes: RouteObject[] = [
  {
    path: "/",
    element: <StoreLayout />,
    children: [{ index: true, element: <HomePage /> }],
  },
];

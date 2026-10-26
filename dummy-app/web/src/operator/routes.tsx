import type { RouteObject } from "react-router";
import { Shell } from "../shared/Shell.tsx";

export const operatorRoutes: RouteObject[] = [
  { path: "/", element: <Shell regionLabel="Operator" surface="Operator" /> },
];

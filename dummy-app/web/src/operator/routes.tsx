import type { RouteObject } from "react-router";
import { OperatorPage } from "./OperatorPage.tsx";

export const operatorRoutes: RouteObject[] = [{ path: "/", element: <OperatorPage /> }];

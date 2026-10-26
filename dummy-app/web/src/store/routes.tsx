import type { RouteObject } from "react-router";
import { Shell } from "../shared/Shell.tsx";

export const storeRoutes: RouteObject[] = [{ path: "/", element: <Shell regionLabel="Storefront" /> }];

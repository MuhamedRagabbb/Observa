import type { RouteObject } from "react-router";
import { CrossAppLink } from "../shared/CrossAppLink.tsx";
import { Shell } from "../shared/Shell.tsx";

export const storeRoutes: RouteObject[] = [
  {
    path: "/",
    element: <Shell regionLabel="Storefront" nav={<CrossAppLink target="control">Experiments</CrossAppLink>} />,
  },
];

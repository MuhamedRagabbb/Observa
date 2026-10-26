import type { RouteObject } from "react-router";
import { CrossAppLink } from "../shared/CrossAppLink.tsx";
import { Shell } from "../shared/Shell.tsx";

export const operatorRoutes: RouteObject[] = [
  {
    path: "/",
    element: (
      <Shell
        regionLabel="Operator"
        surface="Operator"
        nav={<CrossAppLink target="store">Storefront</CrossAppLink>}
      />
    ),
  },
];

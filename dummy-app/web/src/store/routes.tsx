import type { RouteObject } from "react-router";
import { CheckoutPage } from "./checkout/CheckoutPage.tsx";
import { HomePage } from "./home/HomePage.tsx";
import { OrderDetailPage } from "./orders/OrderDetailPage.tsx";
import { OrdersPage } from "./orders/OrdersPage.tsx";
import { StoreLayout } from "./StoreLayout.tsx";

export const storeRoutes: RouteObject[] = [
  {
    path: "/",
    element: <StoreLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "checkout", element: <CheckoutPage /> },
      { path: "orders", element: <OrdersPage /> },
      { path: "orders/:ref", element: <OrderDetailPage /> },
    ],
  },
];

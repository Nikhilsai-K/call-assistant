import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// Public routes don't require auth — landing page and the widget connect API.
const isPublicRoute = createRouteMatcher([
  "/",
  "/sign-in(.*)",
  "/sign-up(.*)",
  "/pay/(.*)",
  "/api/v1/widget/(.*)",
  "/api/v1/webhooks/(.*)",
]);

export default clerkMiddleware(async (auth, req) => {
  if (!isPublicRoute(req)) {
    await auth.protect();
  }
});

export const config = {
  matcher: ["/((?!.+\\.[\\w]+$|_next).*)", "/", "/(api|trpc)(.*)"],
};

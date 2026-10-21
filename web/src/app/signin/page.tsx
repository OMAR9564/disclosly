import { Suspense } from "react";

import { SignIn } from "@/components/SignIn";

/** Sign-in gate; `?error=` and `?return_to=` are read on the client. */
export default function SignInPage() {
  return (
    <Suspense fallback={null}>
      <SignIn />
    </Suspense>
  );
}

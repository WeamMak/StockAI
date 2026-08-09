interface SignInPageProps {
  message?: string;
}

export function SignInPage({ message }: SignInPageProps) {
  return (
    <section aria-labelledby="sign-in-title" className="page-stack sign-in-page">
      <div className="panel">
        <p className="eyebrow">Authorized procurement access</p>
        <h1 id="sign-in-title">Sign in to StockAI</h1>
        <p className="lede">
          Use the managed Cognito login for your fictional procurement officer
          or manager account.
        </p>
        {message === undefined ? null : (
          <p className="notice notice--error" role="alert">
            {message}
          </p>
        )}
        <a className="primary-button sign-in-button" href="/auth/login">
          Sign in with Cognito
        </a>
      </div>
    </section>
  );
}

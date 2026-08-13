# QCC Control Tower 0.8.9 - Render Deployment

This package is an isolated Render-ready copy of Reflex Version 0.8.9. It does
not modify the current Streamlit or Reflex Cloud deployments.

## Render service

- Service type: Web Service
- Runtime: Docker
- Region: Oregon
- Instance: Standard, 2 GB RAM / 1 CPU
- Health check: `/_health`
- Persistent disk: Not required; Supabase remains the system of record

## Required Render environment variables

Copy these values from the working local `.env`; never commit `.env` to GitHub.

- `QCC_SUPABASE_DATABASE_URL`
- `QCC_SUPABASE_URL`
- `QCC_SUPABASE_PUBLISHABLE_KEY`

Do not add `QCC_PUBLIC_APP_URL`. Render supplies `RENDER_EXTERNAL_URL`, and the
application automatically uses it for the Reflex API origin and OAuth callback.

## Supabase redirect URL

After Render assigns the final service address, add this exact redirect URL in
Supabase Authentication URL Configuration:

`https://YOUR-RENDER-SERVICE.onrender.com/auth/callback`

Keep the existing localhost and Reflex Cloud redirect URLs during staging.

## Deployment sequence

1. Push this folder to a private GitHub repository.
2. In Render, select New > Blueprint and connect that repository.
3. Enter the three required environment-variable values when prompted.
4. Confirm the Standard instance and create the service.
5. Add the generated Render callback URL to Supabase.
6. Trigger one manual deploy after the callback URL is allowed.
7. Test Google and Microsoft login in separate private browser sessions.
8. Compare Inventory and Production Planning against Streamlit Version 81.4.

## Safety

Deleting this Render service stops future Render compute charges. It does not
delete Supabase data, GitHub code, local files, Streamlit, or Reflex Cloud.

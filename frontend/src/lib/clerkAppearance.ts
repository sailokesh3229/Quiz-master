// Shared Clerk <SignIn>/<SignUp> theming — maps the design's palette
// (PLAN.md section 9) onto Clerk's prebuilt component variables, since we
// use Clerk's own auth UI rather than rebuilding sign-in/sign-up by hand
// (PLAN.md section 3's locked auth decision).
export const clerkAppearance = {
  variables: {
    colorPrimary: "#26241f",
    colorBackground: "#ffffff",
    colorText: "#26241f",
    colorTextSecondary: "#6e6a62",
    colorInputBackground: "#fbfaf8",
    colorInputText: "#26241f",
    borderRadius: "8px",
    fontFamily: "'Public Sans', sans-serif",
  },
  elements: {
    card: { boxShadow: "0 12px 32px -18px rgba(38,36,31,.22)", border: "1px solid #e2dcd3" },
    formButtonPrimary: {
      backgroundColor: "#26241f",
      "&:hover": { backgroundColor: "#3a372f" },
      fontSize: "15px",
      fontWeight: 600,
    },
    footerActionLink: { color: "#6f8168" },
  },
};

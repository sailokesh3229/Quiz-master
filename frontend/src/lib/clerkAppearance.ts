// Keep Clerk's own auth flow while matching the student app's light palette.
export const clerkAppearance = {
  variables: {
    colorPrimary: "#5265ec",
    colorBackground: "#ffffff",
    colorText: "#182542",
    colorTextSecondary: "#53647f",
    colorInputBackground: "#fbfcff",
    colorInputText: "#182542",
    borderRadius: "12px",
    fontFamily: "'Plus Jakarta Sans', sans-serif",
  },
  elements: {
    card: { boxShadow: "0 25px 65px rgba(38,57,111,.11)", border: "1px solid #e1e7f4", borderRadius: "20px" },
    formButtonPrimary: {
      backgroundColor: "#5265ec",
      "&:hover": { backgroundColor: "#3c50da" },
      fontSize: "15px",
      fontWeight: 600,
    },
    footerActionLink: { color: "#5265ec" },
  },
};

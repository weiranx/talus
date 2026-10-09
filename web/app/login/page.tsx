import LoginForm from "@/components/LoginForm";

export const metadata = { title: "Sign in · Talus" };

export default function LoginPage() {
  return (
    <div style={{ maxWidth: 420, width: "100%" }}>
      <LoginForm />
    </div>
  );
}

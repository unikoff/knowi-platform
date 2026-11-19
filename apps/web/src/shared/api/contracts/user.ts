export type UserProfile = {
  id: number;
  username: string;
  account_ref?: string;
  email: string;
  verified: boolean;
  subscription_until?: string | null;
  subscription_plan?: string | null;
  has_active_subscription?: boolean;
  is_admin?: boolean;
  role?: "user" | "leader" | "admin";
  capabilities?: string[];
  course_id?: string | null;
  auth_revision?: number;
};

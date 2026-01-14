import { useMutation } from "@tanstack/react-query";
import { loginCall, LoginRequest } from "@/components/networking";

export const useLogin = () => {
  return useMutation({
    mutationFn: async ({ username, password, loginMethod }: LoginRequest) => {
      const result = await loginCall(username, password, loginMethod);
      return result;
    },
  });
};

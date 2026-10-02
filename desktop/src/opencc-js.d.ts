declare module "opencc-js" {
  export function Converter(options: { from: "cn"; to: "hk" }): (text: string) => string;
}

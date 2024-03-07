export interface Poll {
  readonly poll_options: Array<string>,
  readonly poll_question: string,
  readonly option_numbers: Array<number>,
  readonly poll_id: number
}

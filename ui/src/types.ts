export type LotStatus = 'active' | 'sold' | 'if_bid' | 'passed' | 'removed' | string

export interface Lot {
  stock_number: string
  stock_id?: number | null
  year?: number | null
  make?: string | null
  model?: string | null
  engine?: string | null
  fuel_type?: string | null
  transmission?: string | null
  odometer?: number | null
  odometer_unit?: string | null
  primary_damage?: string | null
  secondary_damage?: string | null
  title_brand?: string | null
  title_brand_type?: string | null
  damage_estimate?: number | null
  condition_text?: string | null
  runs?: boolean | null
  starts?: boolean | null
  has_keys?: boolean | null
  branch_id?: number | null
  branch_name?: string | null
  location?: string | null
  location_name?: string | null
  province?: string | null
  auction_date?: string | null
  auction_datetime_display?: string | null
  auction_type?: string | null
  auction_type_desc?: string | null
  high_prebid?: number | null
  timed_high_bid?: number | null
  buy_now_price?: number | null
  final_price?: number | null
  currency?: string | null
  status?: LotStatus | null
  item_status_desc?: string | null
  image_url?: string | null
  thumbnail_href?: string | null
  last_seen?: string | null
  vin?: string | null
  lane?: string | null
  sequence?: number | null
}

export interface LotsListResponse {
  total: number
  limit: number
  offset: number
  count: number
  next_cursor: string | null
  next_offset: number | null
  results: Lot[]
}

export interface StatusFacet {
  status: string
  count: number
}

export interface BranchFacet {
  branch_id?: number | null
  branch_name?: string | null
  count: number
}

export interface FiltersResponse {
  makes: string[]
  models: string[]
  years: number[]
  provinces: string[]
  title_brand_types: string[]
  auction_types: string[]
  primary_damages: string[]
  secondary_damages: string[]
  statuses: StatusFacet[]
  branches: BranchFacet[]
}

export interface CrawlReport {
  crawl_status?: string
  pages?: number
  total_seen?: number
  total_expected?: number
  ontario_seen?: number
  inserted?: number
  updated?: number
  unchanged?: number
  archived?: number
  note?: string | null
}

export interface CrawlJob {
  job_id: string
  status: string
  started_at?: string | null
  finished_at?: string | null
  error?: string | null
  report?: CrawlReport | null
  settings?: Record<string, unknown>
}

export interface CrawlStatus {
  running: boolean
  job: CrawlJob | null
}

export interface StatsResponse {
  total_lots: number
  by_branch: { branch_name?: string | null; branch_id?: number | null; n: number }[]
  last_run?: { status?: string; finished_at?: string; ontario_seen?: number } | null
}

export interface PriceHistoryEntry {
  id: number
  observed_at: string
  price_type: string
  amount?: number | null
  currency?: string | null
}

export interface LotFilters {
  status: string
  make: string
  branch_id: string
  primary_damage: string
  title_brand_type: string
  keyword: string
  year_min: string
  year_max: string
  runs: '' | 'true' | 'false'
  starts: '' | 'true' | 'false'
  has_keys: '' | 'true' | 'false'
  sort: string
  descending: boolean
}

export const DEFAULT_FILTERS: LotFilters = {
  status: 'all',
  make: '',
  branch_id: '',
  primary_damage: '',
  title_brand_type: '',
  keyword: '',
  year_min: '',
  year_max: '',
  runs: '',
  starts: '',
  has_keys: '',
  sort: 'auction_date',
  descending: true,
}

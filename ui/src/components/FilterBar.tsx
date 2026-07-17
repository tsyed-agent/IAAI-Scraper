import type { FiltersResponse, LotFilters } from '../types'

export function FilterBar({
  filters,
  facets,
  onChange,
}: {
  filters: LotFilters
  facets: FiltersResponse | null
  onChange: (next: LotFilters) => void
}) {
  const set = <K extends keyof LotFilters>(key: K, value: LotFilters[K]) => {
    onChange({ ...filters, [key]: value })
  }

  return (
    <div className="filter-bar">
      <div className="filter-search">
        <span className="icon" aria-hidden>
          ⌕
        </span>
        <input
          type="search"
          placeholder="Search make, model, damage, stock…"
          value={filters.keyword}
          onChange={(e) => set('keyword', e.target.value)}
          aria-label="Search lots"
        />
      </div>

      <div className="filter-chips" role="toolbar" aria-label="Filters">
        {(
          [
            ['all', 'All'],
            ['active', 'Active'],
            ['sold', 'Sold'],
            ['if_bid', 'If bid'],
            ['passed', 'Passed'],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={`chip ${filters.status === value ? 'active' : ''}`}
            onClick={() => set('status', value)}
          >
            {label}
          </button>
        ))}

        <label className="chip">
          Make
          <select
            value={filters.make}
            onChange={(e) => set('make', e.target.value)}
            aria-label="Filter by make"
          >
            <option value="">Any</option>
            {(facets?.makes || []).map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>

        <label className="chip">
          Branch
          <select
            value={filters.branch_id}
            onChange={(e) => set('branch_id', e.target.value)}
            aria-label="Filter by branch"
          >
            <option value="">Any</option>
            {(facets?.branches || []).map((b) => (
              <option key={`${b.branch_id}-${b.branch_name}`} value={String(b.branch_id ?? '')}>
                {b.branch_name || b.branch_id} ({b.count})
              </option>
            ))}
          </select>
        </label>

        <label className="chip">
          Damage
          <select
            value={filters.primary_damage}
            onChange={(e) => set('primary_damage', e.target.value)}
            aria-label="Filter by damage"
          >
            <option value="">Any</option>
            {(facets?.primary_damages || []).map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>

        <label className="chip">
          Title
          <select
            value={filters.title_brand_type}
            onChange={(e) => set('title_brand_type', e.target.value)}
            aria-label="Filter by title"
          >
            <option value="">Any</option>
            {(facets?.title_brand_types || []).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>

        <button
          type="button"
          className={`chip ${filters.runs === 'true' ? 'active' : ''}`}
          onClick={() => set('runs', filters.runs === 'true' ? '' : 'true')}
        >
          Runs
        </button>
        <button
          type="button"
          className={`chip ${filters.starts === 'true' ? 'active' : ''}`}
          onClick={() => set('starts', filters.starts === 'true' ? '' : 'true')}
        >
          Starts
        </button>
        <button
          type="button"
          className={`chip ${filters.has_keys === 'true' ? 'active' : ''}`}
          onClick={() => set('has_keys', filters.has_keys === 'true' ? '' : 'true')}
        >
          Keys
        </button>

        <label className="chip">
          Year ≥
          <select
            value={filters.year_min}
            onChange={(e) => set('year_min', e.target.value)}
            aria-label="Minimum year"
          >
            <option value="">Any</option>
            {(facets?.years || []).map((y) => (
              <option key={y} value={String(y)}>
                {y}
              </option>
            ))}
          </select>
        </label>

        <label className="chip">
          Sort
          <select
            value={filters.sort}
            onChange={(e) => set('sort', e.target.value)}
            aria-label="Sort field"
          >
            <option value="auction_date">Auction date</option>
            <option value="last_seen">Last seen</option>
            <option value="year">Year</option>
            <option value="make">Make</option>
            <option value="high_prebid">High prebid</option>
            <option value="final_price">Final price</option>
            <option value="odometer">Odometer</option>
          </select>
        </label>

        <button
          type="button"
          className={`chip ${filters.descending ? 'active' : ''}`}
          onClick={() => set('descending', !filters.descending)}
        >
          {filters.descending ? 'Desc' : 'Asc'}
        </button>
      </div>
    </div>
  )
}
